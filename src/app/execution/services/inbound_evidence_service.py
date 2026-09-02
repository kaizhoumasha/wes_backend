"""InboundEvidence 应用服务。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime  # noqa: TC003
from enum import Enum
from typing import Any, Protocol

from src.app.execution.models.inbound_evidence import (
    InboundEvidence,
    InboundEvidenceApplyStatus,
    InboundEvidenceConflict,
    InboundEvidenceKind,
)
from src.app.execution.repositories.inbound_evidence_repository import inbound_evidence_repository
from src.utils.canonical_json import canonical_json_bytes, canonical_json_digest


class InboundEvidenceIdentityConflictError(ValueError):
    """同一入站身份出现不同载荷。"""


class InboundEvidenceDigestPolicy(str, Enum):
    EXACT = "EXACT"
    UNIFORM_WIRE = "UNIFORM_WIRE"


class InboundEvidenceRepositoryPort(Protocol):
    async def lock_source_identity(self, db: object, source_identity: str) -> None: ...

    async def get_by_source_identity_for_update(
        self,
        db: object,
        source_identity: str,
    ) -> InboundEvidence | None: ...

    async def add(self, db: object, evidence: InboundEvidence) -> InboundEvidence: ...

    async def add_conflict(
        self,
        db: object,
        conflict: InboundEvidenceConflict,
    ) -> InboundEvidenceConflict: ...


@dataclass(frozen=True, slots=True)
class InboundEvidenceAcceptance:
    evidence: InboundEvidence
    duplicate: bool


@dataclass(frozen=True, slots=True)
class InboundEvidenceConflictResult:
    evidence: InboundEvidence
    conflict: InboundEvidenceConflict
    source_identity: str

    def to_exception(self) -> InboundEvidenceIdentityConflictError:
        return InboundEvidenceIdentityConflictError(self.source_identity)


def normalize_payload(
    payload: dict[str, Any],
    *,
    digest_policy: InboundEvidenceDigestPolicy = InboundEvidenceDigestPolicy.EXACT,
) -> tuple[dict[str, Any], str]:
    encoded = canonical_json_bytes(payload)
    normalized = json.loads(encoded)
    if not isinstance(normalized, dict):
        raise TypeError("normalized payload 必须是 JSON object")
    semantic_payload = (
        {key: value for key, value in normalized.items() if key not in {"trace_id", "contract_key", "contract_version"}}
        if digest_policy is InboundEvidenceDigestPolicy.UNIFORM_WIRE
        else normalized
    )
    return normalized, canonical_json_digest(semantic_payload)


class InboundEvidenceService:
    def __init__(self, repository: InboundEvidenceRepositoryPort | None = None) -> None:
        self._repository = repository or inbound_evidence_repository

    async def accept(
        self,
        db: object,
        *,
        kind: InboundEvidenceKind,
        source_identity: str,
        normalized_payload: dict[str, Any],
        received_at: datetime,
        line_run_epoch_id: int | None = None,
        material_execution_id: int | None = None,
        transport_task_id: str | None = None,
        device_code: str | None = None,
        command_code: str | None = None,
        contract_key: str | None = None,
        contract_version: str | None = None,
        operation: str | None = None,
        operation_id: str | None = None,
        apply_status: InboundEvidenceApplyStatus = InboundEvidenceApplyStatus.PENDING,
        digest_policy: InboundEvidenceDigestPolicy = InboundEvidenceDigestPolicy.EXACT,
    ) -> InboundEvidenceAcceptance | InboundEvidenceConflictResult:
        if kind in {InboundEvidenceKind.WMS_EVENT, InboundEvidenceKind.WMS_RESULT}:
            if not operation or not operation_id or source_identity != f"{operation}:{operation_id}":
                raise ValueError("WMS source_identity 必须严格等于 operation + operation_id")
            if transport_task_id is not None:
                raise ValueError("WMS evidence 不得关联 transport_task_id")
        elif kind is InboundEvidenceKind.TRANSPORT_RESULT:
            outcome_version = normalized_payload.get("outcome_version")
            payload_task_id = normalized_payload.get("transport_task_id")
            if (
                not transport_task_id
                or not isinstance(outcome_version, int)
                or isinstance(outcome_version, bool)
                or outcome_version < 1
                or payload_task_id != transport_task_id
                or source_identity != f"transport:{transport_task_id}:outcome:{outcome_version}"
            ):
                raise ValueError("Transport source_identity 必须严格等于 transport_task_id + outcome_version")
            if any(value is not None for value in (device_code, command_code, operation, operation_id)):
                raise ValueError("Transport evidence 不得混入 device/WMS identity")
        elif not device_code:
            raise ValueError("DEVICE evidence 必须关联 device_code")
        elif transport_task_id is not None:
            raise ValueError("DEVICE evidence 不得关联 transport_task_id")
        payload, digest = normalize_payload(
            normalized_payload,
            digest_policy=digest_policy,
        )
        await self._repository.lock_source_identity(db, source_identity)
        existing = await self._repository.get_by_source_identity_for_update(db, source_identity)
        if existing is not None:
            correlations = (
                kind,
                line_run_epoch_id,
                material_execution_id,
                transport_task_id,
                device_code,
                command_code,
                contract_key,
                contract_version,
                operation,
                operation_id,
            )
            existing_correlations = (
                existing.kind,
                existing.line_run_epoch_id,
                existing.material_execution_id,
                existing.transport_task_id,
                existing.device_code,
                existing.command_code,
                existing.contract_key,
                existing.contract_version,
                existing.operation,
                existing.operation_id,
            )
            if existing.payload_digest == digest and (
                existing_correlations == correlations
                or (
                    kind == InboundEvidenceKind.DEVICE_EVENT
                    and existing.kind == InboundEvidenceKind.DEVICE_EVENT
                    and existing.apply_status != InboundEvidenceApplyStatus.IGNORED
                )
            ):
                return InboundEvidenceAcceptance(existing, duplicate=True)
            if existing.id is None:
                raise RuntimeError("持久化 InboundEvidence 缺少主键")
            conflict = await self._add_conflict(
                db,
                first_evidence_id=existing.id,
                source_identity=source_identity,
                payload=payload,
                digest=digest,
                reason_code=(
                    "SOURCE_IDENTITY_PAYLOAD_CONFLICT"
                    if existing.payload_digest != digest
                    else "SOURCE_IDENTITY_CORRELATION_CONFLICT"
                ),
                received_at=received_at,
            )
            return InboundEvidenceConflictResult(
                evidence=existing,
                conflict=conflict,
                source_identity=source_identity,
            )
        evidence = await self._repository.add(
            db,
            InboundEvidence(
                kind=kind,
                source_identity=source_identity,
                payload_digest=digest,
                normalized_payload=payload,
                received_at=received_at,
                line_run_epoch_id=line_run_epoch_id,
                material_execution_id=material_execution_id,
                transport_task_id=transport_task_id,
                device_code=device_code,
                command_code=command_code,
                contract_key=contract_key,
                contract_version=contract_version,
                operation=operation,
                operation_id=operation_id,
                apply_status=apply_status,
            ),
        )
        return InboundEvidenceAcceptance(evidence, duplicate=False)

    async def record_conflict(
        self,
        db: object,
        *,
        first: InboundEvidence,
        source_identity: str,
        normalized_payload: dict[str, Any],
        reason_code: str,
        received_at: datetime,
        digest_policy: InboundEvidenceDigestPolicy = InboundEvidenceDigestPolicy.EXACT,
    ) -> InboundEvidenceConflict:
        if first.id is None:
            raise RuntimeError("持久化 InboundEvidence 缺少主键")
        payload, digest = normalize_payload(
            normalized_payload,
            digest_policy=digest_policy,
        )
        return await self._add_conflict(
            db,
            first_evidence_id=first.id,
            source_identity=source_identity,
            payload=payload,
            digest=digest,
            reason_code=reason_code,
            received_at=received_at,
        )

    async def _add_conflict(
        self,
        db: object,
        *,
        first_evidence_id: int,
        source_identity: str,
        payload: dict[str, Any],
        digest: str,
        reason_code: str,
        received_at: datetime,
    ) -> InboundEvidenceConflict:
        return await self._repository.add_conflict(
            db,
            InboundEvidenceConflict(
                source_identity=source_identity,
                first_evidence_id=first_evidence_id,
                conflicting_digest=digest,
                normalized_payload=payload,
                reason_code=reason_code,
                received_at=received_at,
            ),
        )


inbound_evidence_service = InboundEvidenceService()

__all__ = [
    "InboundEvidenceAcceptance",
    "InboundEvidenceConflictResult",
    "InboundEvidenceDigestPolicy",
    "InboundEvidenceIdentityConflictError",
    "InboundEvidenceService",
    "inbound_evidence_service",
    "normalize_payload",
]
