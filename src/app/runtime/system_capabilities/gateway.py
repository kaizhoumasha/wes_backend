"""Attempt-scoped System Capability QUERY gateway。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
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
_PROFILE_ENVIRONMENTS = frozenset({"sandbox", "staging", "production"})


def _admission_matches_profile(admission: str, profile_identity: str) -> bool:
    """允许 Definition 固定合同族，同时由 binding 固定环境 profile。"""

    if admission == profile_identity:
        return True
    family, separator, environment = profile_identity.rpartition(".")
    return bool(separator) and family == admission and environment in _PROFILE_ENVIRONMENTS


@dataclass(frozen=True, slots=True)
class GatewayLimits:
    """单 attempt 的资源边界。"""

    max_unique_queries: int = 32
    max_input_bytes: int = 64 * 1024
    max_output_bytes: int = 64 * 1024
    max_evidence_bytes: int = 16 * 1024
    max_total_evidence_bytes: int = 128 * 1024

    def __post_init__(self) -> None:
        if (
            min(
                self.max_unique_queries,
                self.max_input_bytes,
                self.max_output_bytes,
                self.max_evidence_bytes,
                self.max_total_evidence_bytes,
            )
            <= 0
        ):
            raise ValueError("gateway limits must be positive")


@dataclass(frozen=True, slots=True)
class GatewayQueryResult:
    """封闭 outcome 与可选的安全 evidence。"""

    outcome: CapabilityOutcome
    evidence: QueryEvidence | None


@dataclass(frozen=True, slots=True)
class AttemptCloseReport:
    """Attempt owner 有界关闭结果。"""

    requested: int
    completed: int
    unterminated: int
    error_code: str | None = None


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
        self._redactor = redactor
        self._authority = authority
        self._source = source
        self._source_version = source_version
        self._admission_snapshot = dict(admission_snapshot or {"profile": admission_profile})
        self._inflight: dict[str, asyncio.Task[GatewayQueryResult]] = {}
        self._tracked_children: set[asyncio.Task[Any]] = set()
        self._cache: dict[str, GatewayQueryResult] = {}
        self._total_evidence_bytes = 0
        self._closed = False

    async def execute(  # noqa: PLR0911
        self,
        capability_key: str,
        contract_version: str,
        input_data: BaseModel | dict[str, Any],
    ) -> GatewayQueryResult:
        """校验声明、输入、Port/profile 与边界后执行一次 canonical QUERY。"""

        if self._closed:
            return self._violation("ATTEMPT_CLOSED", "attempt gateway is closed")

        identity = (capability_key, contract_version)
        definition = self._definitions.get(identity)
        if identity not in self._allowed_capabilities or definition is None:
            return self._violation("CAPABILITY_NOT_DECLARED", "capability is not declared for this plugin")
        if definition.mode is not SystemCapabilityMode.QUERY:
            return self._violation("CAPABILITY_MODE_INVALID", "gateway only executes QUERY capabilities")
        if not _admission_matches_profile(definition.admission, self._admission_profile):
            return self._violation("CAPABILITY_ADMISSION_DENIED", "capability admission profile does not match")
        try:
            request = definition.input_model.model_validate(input_data)
            self._context.require_query_ports(definition.required_ports)
        except (KeyError, PermissionError, TypeError, ValidationError, ValueError) as exc:
            return self._violation("CAPABILITY_CONTRACT_INVALID", _safe_contract_message(exc))

        request_payload = request.model_dump(mode="json")
        if _canonical_bytes(request_payload) > self._limits.max_input_bytes:
            return self._violation("QUERY_INPUT_LIMIT_EXCEEDED", "canonical query input size limit exceeded")

        query_key = sha256_digest(
            {
                "attempt_id": self.attempt_id,
                "definition": definition.identity,
                "input": request_payload,
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
        task.add_done_callback(lambda completed: self._complete_query(query_key, completed))
        return await asyncio.shield(task)

    async def aclose(self, *, grace_seconds: float = 0.05) -> AttemptCloseReport:
        """取消 attempt tasks，并且只在有限 grace 内等待协作回收。"""

        if not isfinite(grace_seconds) or grace_seconds < 0:
            raise ValueError("attempt close grace_seconds must be finite and non-negative")
        self._closed = True
        tasks = set(self._inflight.values()) | set(self._tracked_children)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=grace_seconds)
        else:
            done, pending = set(), set()
        for task in done:
            if task in self._tracked_children:
                self._consume_child(task)
        for query_key, task in tuple(self._inflight.items()):
            if task in done:
                self._inflight.pop(query_key, None)
        unterminated = len(pending)
        return AttemptCloseReport(
            requested=len(tasks),
            completed=len(done),
            unterminated=unterminated,
            error_code="ATTEMPT_CLOSE_UNTERMINATED" if unterminated else None,
        )

    def _complete_query(self, query_key: str, task: asyncio.Task[GatewayQueryResult]) -> None:
        """由底层 task 唯一负责 cache/cleanup，waiter 取消不能破坏共享查询。"""

        if self._inflight.get(query_key) is not task:
            return
        self._inflight.pop(query_key, None)
        if task.cancelled():
            return
        try:
            self._cache[query_key] = task.result()
        except BaseException:
            # task 自身异常/取消时不缓存；下一次调用可重新建立干净查询。
            return

    async def _execute_once(
        self,
        definition: SystemCapabilityDefinition,
        request: BaseModel,
    ) -> GatewayQueryResult:
        ports = tuple(self._context.get_query_port(port) for port in definition.required_ports)
        handler = definition.handler_factory(*ports)
        child = asyncio.create_task(handler(request))
        self._tracked_children.add(child)
        child.add_done_callback(self._consume_child)
        done, _ = await asyncio.wait({child}, timeout=definition.timeout_seconds)
        if child not in done:
            child.cancel()
            outcome = RetryableFailure(error_code="TIMEOUT", message="system capability query timed out")
        else:
            try:
                raw_outcome = child.result()
            except PermissionError:
                outcome = ContractViolation(
                    error_code="CAPABILITY_PORT_ACCESS_DENIED",
                    message="capability attempted an undeclared Port method",
                )
            else:
                outcome = _normalize_outcome(raw_outcome, output_model=definition.output_model)
        if _canonical_bytes(_bounded_output_value(outcome)) > self._limits.max_output_bytes:
            return self._violation("QUERY_OUTPUT_LIMIT_EXCEEDED", "canonical query output size limit exceeded")
        return self._attach_evidence(definition, request, outcome)

    def _consume_child(self, task: asyncio.Task[Any]) -> None:
        """消费 child 最终异常并清理跟踪，避免 orphan warning。"""

        self._tracked_children.discard(task)
        if task.cancelled():
            return
        try:
            _ = task.exception()
        except BaseException:
            return

    def _attach_evidence(
        self,
        definition: SystemCapabilityDefinition,
        request: BaseModel,
        outcome: CapabilityOutcome,
    ) -> GatewayQueryResult:
        raw_output = outcome.model_dump(mode="json")
        try:
            redacted = self._redact_summary(definition, raw_output)
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
                shadow_expected=None,
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

    def _redact_summary(
        self,
        definition: SystemCapabilityDefinition,
        raw_output: dict[str, Any],
    ) -> dict[str, Any]:
        """metadata policy 只保留类型、稳定码与大小，绝不保留业务值。"""

        if definition.audit_policy not in {"metadata", "redacted"}:
            raise ValueError("unsupported audit policy")
        if definition.audit_policy == "redacted":
            if self._redactor is None:
                raise ValueError("redacted audit policy requires an explicit redactor")
            explicitly_redacted = self._redactor(raw_output)
            if not isinstance(explicitly_redacted, dict):
                raise TypeError("redactor must return an object")
            return explicitly_redacted
        # metadata policy 仍执行显式 redactor 作为可用性门禁，但不会持久化其业务字段。
        if self._redactor is not None and not isinstance(self._redactor(raw_output), dict):
            raise TypeError("redactor must return an object")
        stable_code = raw_output.get("reason_code") or raw_output.get("error_code")
        summary: dict[str, Any] = {
            "kind": raw_output.get("kind", "unknown"),
            "payload_type": type(raw_output.get("payload")).__name__,
            "payload_bytes": len(canonical_json(raw_output).encode("utf-8")),
        }
        if isinstance(stable_code, str):
            summary["code"] = stable_code
        return summary

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


def _bounded_output_value(outcome: CapabilityOutcome) -> Any:
    if isinstance(outcome, Success):
        payload = outcome.payload
        return payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
    return outcome.model_dump(mode="json")


def _canonical_bytes(value: Any) -> int:
    return len(canonical_json(value).encode("utf-8"))


__all__ = ["AttemptCloseReport", "GatewayLimits", "GatewayQueryResult", "SystemCapabilityGateway"]
