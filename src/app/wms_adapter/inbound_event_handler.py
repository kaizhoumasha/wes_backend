"""粗分机 WMS 人工对账事件接收。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime  # noqa: TC003
from typing import TYPE_CHECKING, Any, Protocol, TypeGuard, cast

from pydantic import ValidationError

from src.app.execution.models import (
    InboundEvidenceApplyStatus,
    InboundEvidenceExecutionBinding,
    InboundEvidenceKind,
    MaterialExecution,
)
from src.app.execution.repositories import (
    inbound_evidence_execution_binding_repository,
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
    RECONCILIATION_OPERATION,
    ReconciliationEvent,
    parse_reconciliation_event,
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
        envelope: ReconciliationEvent,
        *,
        received_at: datetime,
    ) -> InboundEventPersistenceResult: ...


class InboundEventCorrelationError(ValueError):
    """对账事件无法完整关联全部已持久化 execution。"""


class _MaterialExecutionRepository(Protocol):
    async def get_by_execution_code_for_update(
        self,
        db: object,
        execution_code: str,
    ) -> MaterialExecution | None: ...


class _EvidenceExecutionBindingRepository(Protocol):
    async def list_for_evidence_for_update(
        self,
        db: object,
        evidence_id: int,
    ) -> list[InboundEvidenceExecutionBinding]: ...

    async def add(
        self,
        db: object,
        binding: InboundEvidenceExecutionBinding,
    ) -> InboundEvidenceExecutionBinding: ...


class InboundEventEvidenceRecorder:
    """在一个短事务中只保存 WMS_EVENT，不应用人工决定。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        evidence_service: InboundEvidenceService | None = None,
        material_execution_repository: _MaterialExecutionRepository | None = None,
        evidence_execution_binding_repository: _EvidenceExecutionBindingRepository | None = None,
    ) -> None:
        self._sessions = session_factory
        self._evidence = evidence_service or InboundEvidenceService()
        self._executions = material_execution_repository or default_material_execution_repository
        self._bindings = evidence_execution_binding_repository or inbound_evidence_execution_binding_repository

    async def record(
        self,
        envelope: ReconciliationEvent,
        *,
        received_at: datetime,
    ) -> InboundEventPersistenceResult:
        payload = envelope.model_dump(mode="json")
        source_identity = f"{envelope.operation}:{envelope.operation_id}"
        async with self._sessions.begin() as db:
            result = await self._evidence.accept(
                db,
                kind=InboundEvidenceKind.WMS_EVENT,
                source_identity=source_identity,
                normalized_payload=payload,
                received_at=received_at,
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
                    raise RuntimeError("持久化 reconciliation evidence 缺少主键")
                await self._freeze_execution_bindings(db, result.evidence.id, envelope)
                persisted_code = "DUPLICATE" if result.duplicate else "RECEIVED"
                timestamp_ms = int(timezone.to_utc(result.evidence.received_at).timestamp() * 1000)
        return InboundEventPersistenceResult(code=persisted_code, timestamp_ms=timestamp_ms)

    async def _freeze_execution_bindings(
        self,
        db: object,
        evidence_id: int,
        envelope: ReconciliationEvent,
    ) -> None:
        positions = {item.material_execution_id: item for item in envelope.data.authoritative_positions}
        execution_ids: list[int] = []
        for execution_code in envelope.data.affected_execution_ids:
            execution = await self._executions.get_by_execution_code_for_update(db, execution_code)
            position = positions[execution_code]
            if execution is None or execution.id is None or execution.material_trace_id != position.material_trace_id:
                raise InboundEventCorrelationError(execution_code)
            execution_ids.append(execution.id)

        existing = await self._bindings.list_for_evidence_for_update(db, evidence_id)
        if existing:
            frozen = [(binding.ordinal, binding.material_execution_id) for binding in existing]
            expected = list(enumerate(execution_ids))
            if frozen != expected:
                raise InboundEventCorrelationError("frozen reconciliation bindings conflict")
            return
        for ordinal, execution_id in enumerate(execution_ids):
            await self._bindings.add(
                db,
                InboundEvidenceExecutionBinding(
                    inbound_evidence_id=evidence_id,
                    material_execution_id=execution_id,
                    ordinal=ordinal,
                ),
            )


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
        if operation != RECONCILIATION_OPERATION:
            return _ack(422, operation_id, "REJECTED", received_at, {"reason_code": "UNSUPPORTED_OPERATION"})
        try:
            envelope = parse_reconciliation_event(raw_envelope)
        except (ValidationError, ValueError, TypeError):
            return _ack(422, operation_id, "REJECTED", received_at, {"reason_code": "INVALID_DATA"})
        try:
            persisted = await self._recorder.record(envelope, received_at=timezone.now_for_db())
        except InboundEventCorrelationError:
            return _ack(422, operation_id, "REJECTED", received_at, {"reason_code": "INVALID_EXECUTION_CORRELATION"})
        except Exception:
            logger.exception("WMS 对账事件持久化失败: operation_id=%s", operation_id)
            return _ack(503, operation_id, "UNAVAILABLE", received_at, {})
        status_by_code = {"RECEIVED": 202, "DUPLICATE": 200, "CONFLICT": 409}
        http_status = status_by_code.get(persisted.code)
        if http_status is None:
            logger.warning("WMS 对账事件持久化返回未知 code: %s", persisted.code)
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
