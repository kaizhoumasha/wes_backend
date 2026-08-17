"""粗分机 WMS 单对象恢复事件接收。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime  # noqa: TC003
from typing import TYPE_CHECKING, Any, Protocol, TypeGuard, cast

from pydantic import ValidationError

from src.app.execution.models import (
    InboundEvidenceApplyStatus,
    InboundEvidenceKind,
    MaterialExecution,
    MaterialExecutionStatus,
)
from src.app.execution.repositories import (
    inbound_evidence_repository as default_inbound_evidence_repository,
)
from src.app.execution.repositories import (
    material_execution_repository as default_material_execution_repository,
)
from src.app.execution.services import (
    InboundEvidenceConflictResult,
    InboundEvidenceService,
)
from src.app.wms_adapter.inbound_wire import (
    MAX_INBOUND_BODY_BYTES,
    RECOVERY_OPERATION,
    RecoveryEvent,
    parse_recovery_event,
)
from src.app.wms_adapter.strict_json import StrictJsonError, loads_transport_json
from src.core.uuid7 import is_uuid7
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class InboundEventResponse:
    http_status: int
    body: dict[str, Any]


@dataclass(frozen=True, slots=True)
class InboundEventPersistenceResult:
    code: str
    timestamp_ms: int


class _InboundEventRecorder(Protocol):
    async def record(
        self,
        envelope: RecoveryEvent,
        *,
        received_at: datetime,
    ) -> InboundEventPersistenceResult: ...


class InboundEventCorrelationError(ValueError):
    """恢复事件不匹配当前 execution 因果围栏。"""


class _MaterialExecutionRepository(Protocol):
    async def get_by_execution_code_for_update(
        self,
        db: object,
        execution_code: str,
    ) -> MaterialExecution | None: ...


class _InboundEvidenceRepository(Protocol):
    async def get_by_id_for_update(self, db: object, evidence_id: int) -> object | None: ...


class InboundEventEvidenceRecorder:
    """在一个短事务中只保存 WMS_EVENT，不应用人工决定。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        evidence_service: InboundEvidenceService | None = None,
        material_execution_repository: _MaterialExecutionRepository | None = None,
        inbound_evidence_repository: _InboundEvidenceRepository | None = None,
    ) -> None:
        self._sessions = session_factory
        self._evidence = evidence_service or InboundEvidenceService()
        self._executions = cast(
            "_MaterialExecutionRepository",
            material_execution_repository or default_material_execution_repository,
        )
        self._evidences = cast(
            "_InboundEvidenceRepository",
            inbound_evidence_repository or default_inbound_evidence_repository,
        )

    async def record(
        self,
        envelope: RecoveryEvent,
        *,
        received_at: datetime,
    ) -> InboundEventPersistenceResult:
        payload = envelope.model_dump(mode="json")
        source_identity = f"{envelope.operation}:{envelope.operation_id}"
        async with self._sessions.begin() as db:
            execution = await self._executions.get_by_execution_code_for_update(
                db,
                envelope.data.material_execution_id,
            )
            if (
                execution is None
                or execution.id is None
                or execution.material_trace_id != envelope.data.material_trace_id
            ):
                raise InboundEventCorrelationError(envelope.data.material_execution_id)
            result = await self._evidence.accept(
                db,
                kind=InboundEvidenceKind.WMS_EVENT,
                source_identity=source_identity,
                normalized_payload=payload,
                received_at=received_at,
                line_run_epoch_id=execution.line_run_epoch_id,
                material_execution_id=execution.id,
                contract_key="rough_sorter_inbound",
                contract_version="1.0",
                operation=envelope.operation,
                operation_id=envelope.operation_id,
                apply_status=InboundEvidenceApplyStatus.APPLIED,
            )
            if isinstance(result, InboundEvidenceConflictResult):
                persisted_code = "CONFLICT"
                timestamp_ms = int(timezone.to_utc(result.conflict.received_at).timestamp() * 1000)
            else:
                if result.evidence.id is None:
                    raise RuntimeError("持久化 recovery evidence 缺少主键")
                if not result.duplicate:
                    await self._validate_causal_fence(db, execution, envelope.data.reconciling_evidence_id)
                persisted_code = "DUPLICATE" if result.duplicate else "RECEIVED"
                timestamp_ms = int(timezone.to_utc(result.evidence.received_at).timestamp() * 1000)
        return InboundEventPersistenceResult(code=persisted_code, timestamp_ms=timestamp_ms)

    async def _validate_causal_fence(
        self,
        db: object,
        execution: MaterialExecution,
        reconciling_evidence_id: str,
    ) -> None:
        if not reconciling_evidence_id.isascii() or not reconciling_evidence_id.isdigit():
            raise InboundEventCorrelationError(reconciling_evidence_id)
        causal_id = int(reconciling_evidence_id)
        causal = await self._evidences.get_by_id_for_update(db, causal_id)
        if (
            MaterialExecutionStatus(execution.status) is not MaterialExecutionStatus.RECONCILING
            or execution.last_transition_evidence_id != causal_id
            or causal is None
            or getattr(causal, "id", None) != causal_id
            or getattr(causal, "material_execution_id", None) != execution.id
            or getattr(causal, "line_run_epoch_id", None) != execution.line_run_epoch_id
        ):
            raise InboundEventCorrelationError(reconciling_evidence_id)


class InboundEventHandler:
    def __init__(self, recorder: _InboundEventRecorder) -> None:
        self._recorder = recorder

    async def handle(self, raw_body: bytes) -> InboundEventResponse:  # noqa: PLR0911 - 入口按关联阶段明确拒绝。
        if len(raw_body) > MAX_INBOUND_BODY_BYTES:
            return InboundEventResponse(413, {})
        try:
            raw_value = loads_transport_json(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, StrictJsonError):
            return InboundEventResponse(400, {})
        if not isinstance(raw_value, dict):
            return InboundEventResponse(400, {})
        raw_envelope = cast("dict[str, Any]", raw_value)
        operation_id = raw_envelope.get("operation_id")
        operation = raw_envelope.get("operation")
        if not _is_operation_id(operation_id) or not _is_operation(operation):
            return InboundEventResponse(400, {})
        received_at = timezone.now_utc()
        if operation != RECOVERY_OPERATION:
            return _ack(422, operation_id, "REJECTED", received_at, {"reason_code": "UNSUPPORTED_OPERATION"})
        try:
            envelope = parse_recovery_event(raw_envelope)
        except (ValidationError, ValueError, TypeError):
            return _ack(422, operation_id, "REJECTED", received_at, {"reason_code": "INVALID_DATA"})
        try:
            persisted = await self._recorder.record(envelope, received_at=timezone.now_for_db())
        except InboundEventCorrelationError:
            return _ack(422, operation_id, "REJECTED", received_at, {"reason_code": "INVALID_EXECUTION_CORRELATION"})
        except Exception:
            logger.exception("WMS recovery 事件持久化失败: operation_id=%s", operation_id)
            return _ack(503, operation_id, "UNAVAILABLE", received_at, {})
        status_by_code = {"RECEIVED": 202, "DUPLICATE": 200, "CONFLICT": 409}
        http_status = status_by_code.get(persisted.code)
        if http_status is None:
            logger.warning("WMS recovery 事件持久化返回未知 code: %s", persisted.code)
            return _ack(503, operation_id, "UNAVAILABLE", received_at, {})
        return InboundEventResponse(
            http_status,
            {
                "operation_id": operation_id,
                "code": persisted.code,
                "timestamp": persisted.timestamp_ms,
                "data": {},
            },
        )


def _ack(
    http_status: int,
    operation_id: str,
    code: str,
    at: datetime,
    data: dict[str, Any],
) -> InboundEventResponse:
    return InboundEventResponse(
        http_status,
        {
            "operation_id": operation_id,
            "code": code,
            "timestamp": int(at.timestamp() * 1000),
            "data": data,
        },
    )


def _is_operation_id(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and value == value.lower() and is_uuid7(value)


def _is_operation(value: object) -> TypeGuard[str]:
    if not isinstance(value, str) or not value.strip() or "\x00" in value or len(value) > 160:
        return False
    try:
        _ = value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


__all__ = [
    "InboundEventCorrelationError",
    "InboundEventEvidenceRecorder",
    "InboundEventHandler",
    "InboundEventPersistenceResult",
    "InboundEventResponse",
]
