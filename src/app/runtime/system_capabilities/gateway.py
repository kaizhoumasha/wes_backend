"""Attempt-scoped System Capability QUERY gateway。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from src.app.runtime.extension_identity import canonical_json, sha256_digest
from src.app.runtime.system_capabilities.definition import SystemCapabilityDefinition, SystemCapabilityMode
from src.app.runtime.system_capabilities.evidence import QueryEvidence
from src.app.runtime.system_capabilities.outcomes import (
    BusinessReject,
    ContractViolation,
    RetryableFailure,
    Success,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from src.app.runtime.capability_port_registry import RuntimeCapabilityContext

type CapabilityOutcome = Success[Any] | BusinessReject | RetryableFailure | ContractViolation


@dataclass(frozen=True, slots=True)
class GatewayLimits:
    """单 attempt 的资源边界。"""

    max_unique_queries: int = 32
    max_evidence_bytes: int = 16 * 1024
    max_total_evidence_bytes: int = 128 * 1024

    def __post_init__(self) -> None:
        if min(self.max_unique_queries, self.max_evidence_bytes, self.max_total_evidence_bytes) <= 0:
            raise ValueError("gateway limits must be positive")


@dataclass(frozen=True, slots=True)
class GatewayQueryResult:
    """封闭 outcome 与可选的安全 evidence。"""

    outcome: CapabilityOutcome
    evidence: QueryEvidence | None


class SystemCapabilityGateway:
    """每个 Inbox attempt 新建；cache 与 in-flight 不跨 attempt。"""

    def __init__(
        self,
        *,
        attempt_id: str,
        definitions: Mapping[tuple[str, str], SystemCapabilityDefinition],
        allowed_capabilities: frozenset[tuple[str, str]],
        context: RuntimeCapabilityContext,
        admission_profile: str,
        limits: GatewayLimits | None = None,
        redactor: Callable[[object], object] | None = None,
        authority: str = "RUNTIME",
        source: str = "system-capability",
        source_version: str = "runtime",
        admission_snapshot: Mapping[str, Any] | None = None,
    ) -> None:
        if not attempt_id:
            raise ValueError("attempt_id is required")
        self.attempt_id = attempt_id
        self._definitions = dict(definitions)
        self._allowed_capabilities = allowed_capabilities
        self._context = context
        self._admission_profile = admission_profile
        self._limits = limits or GatewayLimits()
        self._redactor = redactor or (lambda value: value)
        self._authority = authority
        self._source = source
        self._source_version = source_version
        self._admission_snapshot = dict(admission_snapshot or {"profile": admission_profile})
        self._inflight: dict[str, asyncio.Task[GatewayQueryResult]] = {}
        self._cache: dict[str, GatewayQueryResult] = {}
        self._total_evidence_bytes = 0

    async def execute(
        self,
        capability_key: str,
        contract_version: str,
        input_data: BaseModel | dict[str, Any],
    ) -> GatewayQueryResult:
        """校验声明、输入、Port/profile 与边界后执行一次 canonical QUERY。"""

        identity = (capability_key, contract_version)
        definition = self._definitions.get(identity)
        if identity not in self._allowed_capabilities or definition is None:
            return self._violation("CAPABILITY_NOT_DECLARED", "capability is not declared for this plugin")
        if definition.mode is not SystemCapabilityMode.QUERY:
            return self._violation("CAPABILITY_MODE_INVALID", "gateway only executes QUERY capabilities")
        if definition.admission != self._admission_profile:
            return self._violation("CAPABILITY_ADMISSION_DENIED", "capability admission profile does not match")
        try:
            request = definition.input_model.model_validate(input_data)
            self._context.require_query_ports(definition.required_ports)
        except (KeyError, PermissionError, TypeError, ValidationError, ValueError) as exc:
            return self._violation("CAPABILITY_CONTRACT_INVALID", _safe_contract_message(exc))

        query_key = sha256_digest(
            {
                "attempt_id": self.attempt_id,
                "definition": definition.identity,
                "input": request.model_dump(mode="json"),
            }
        )
        if query_key in self._cache:
            return self._cache[query_key]
        if query_key in self._inflight:
            return await asyncio.shield(self._inflight[query_key])
        if len(self._cache) + len(self._inflight) >= self._limits.max_unique_queries:
            return self._violation("QUERY_LIMIT_EXCEEDED", "unique query limit exceeded")

        task = asyncio.create_task(self._execute_once(definition, request))
        self._inflight[query_key] = task
        try:
            result = await asyncio.shield(task)
            self._cache[query_key] = result
            return result
        finally:
            self._inflight.pop(query_key, None)

    async def _execute_once(
        self,
        definition: SystemCapabilityDefinition,
        request: BaseModel,
    ) -> GatewayQueryResult:
        try:
            handler = definition.handler_factory(self._context)
            raw_outcome = await asyncio.wait_for(handler(request), timeout=definition.timeout_seconds)
            outcome = _normalize_outcome(raw_outcome, output_model=definition.output_model)
        except TimeoutError:
            outcome = RetryableFailure(error_code="TIMEOUT", message="system capability query timed out")
        except Exception:
            outcome = RetryableFailure(error_code="UNKNOWN", message="system capability query failed")
        return self._attach_evidence(definition, request, outcome)

    def _attach_evidence(
        self,
        definition: SystemCapabilityDefinition,
        request: BaseModel,
        outcome: CapabilityOutcome,
    ) -> GatewayQueryResult:
        raw_output = outcome.model_dump(mode="json")
        try:
            redacted = self._redactor(raw_output)
            if not isinstance(redacted, dict):
                raise TypeError("redactor must return an object")
            evidence = QueryEvidence(
                capability_key=definition.capability_key,
                contract_version=definition.contract_version,
                input_hash=sha256_digest(request.model_dump(mode="json")),
                output_hash=sha256_digest(raw_output),
                authority=self._authority,
                source=self._source,
                evidence_at=datetime.now(UTC),
                source_version=self._source_version,
                admission_snapshot=self._admission_snapshot,
                summary={"outcome": redacted},
            )
            evidence_bytes = len(canonical_json(evidence.model_dump(mode="json")).encode("utf-8"))
        except Exception:
            return self._violation("EVIDENCE_REDACTION_FAILED", "evidence redaction failed")
        if evidence_bytes > self._limits.max_evidence_bytes:
            return self._violation("EVIDENCE_ITEM_LIMIT_EXCEEDED", "single evidence size limit exceeded")
        if self._total_evidence_bytes + evidence_bytes > self._limits.max_total_evidence_bytes:
            return self._violation("EVIDENCE_TOTAL_LIMIT_EXCEEDED", "total evidence size limit exceeded")
        self._total_evidence_bytes += evidence_bytes
        return GatewayQueryResult(outcome=outcome, evidence=evidence)

    @staticmethod
    def _violation(error_code: str, message: str) -> GatewayQueryResult:
        return GatewayQueryResult(
            outcome=ContractViolation(error_code=error_code, message=message),
            evidence=None,
        )


def _normalize_outcome(raw: object, *, output_model: type[BaseModel]) -> CapabilityOutcome:
    if isinstance(raw, Success):
        try:
            return Success(payload=output_model.model_validate(raw.payload))
        except ValidationError:
            return ContractViolation(error_code="OUTPUT_CONTRACT_INVALID", message="success payload is invalid")
    if isinstance(raw, BusinessReject | RetryableFailure | ContractViolation):
        return raw
    try:
        return Success(payload=output_model.model_validate(raw))
    except ValidationError:
        return ContractViolation(error_code="OUTPUT_CONTRACT_INVALID", message="handler output is invalid")


def _safe_contract_message(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "capability input validation failed"
    return type(exc).__name__


__all__ = ["GatewayLimits", "GatewayQueryResult", "SystemCapabilityGateway"]
