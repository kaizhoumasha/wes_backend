"""PickingTask 发布事件的原子 evidence 与业务持久化。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.app.execution.models import InboundEvidenceApplyStatus, InboundEvidenceKind
from src.app.execution.services import InboundEvidenceConflictResult, InboundEvidenceService
from src.app.wms_adapter.outbound_picking.event_handler import PickingTaskIssuedPersistenceResult
from src.app.wms_integration.outbound_picking.models import PickingTask
from src.app.wms_integration.outbound_picking.repositories import PickingTaskRepository, picking_task_repository
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.wms_adapter.outbound_picking.wire import PickingTaskIssuedEvent


class PickingTaskIssuedService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        evidence_service: InboundEvidenceService | None = None,
        task_repository: PickingTaskRepository | None = None,
    ) -> None:
        self._sessions = session_factory
        self._evidence = evidence_service or InboundEvidenceService()
        self._tasks = task_repository or picking_task_repository

    async def record(
        self,
        envelope: PickingTaskIssuedEvent,
        *,
        received_at: datetime,
    ) -> PickingTaskIssuedPersistenceResult:
        source_identity = f"{envelope.operation}:{envelope.operation_id}"
        payload = envelope.model_dump(mode="json")
        async with self._sessions.begin() as db:
            acceptance = await self._evidence.accept(
                db,
                kind=InboundEvidenceKind.WMS_EVENT,
                source_identity=source_identity,
                normalized_payload=payload,
                received_at=received_at,
                contract_key=envelope.operation,
                contract_version="1.0",
                operation=envelope.operation,
                operation_id=envelope.operation_id,
                apply_status=InboundEvidenceApplyStatus.APPLIED,
            )
            if isinstance(acceptance, InboundEvidenceConflictResult):
                return PickingTaskIssuedPersistenceResult(
                    code="CONFLICT",
                    timestamp_ms=_timestamp_ms(acceptance.evidence.received_at),
                    reason_code="IDEMPOTENCY_CONFLICT",
                )
            evidence = acceptance.evidence
            if acceptance.duplicate:
                if InboundEvidenceApplyStatus(evidence.apply_status) is InboundEvidenceApplyStatus.APPLIED:
                    return PickingTaskIssuedPersistenceResult(
                        code="DUPLICATE",
                        timestamp_ms=_timestamp_ms(evidence.received_at),
                    )
                if InboundEvidenceApplyStatus(evidence.apply_status) is InboundEvidenceApplyStatus.RECONCILING:
                    return PickingTaskIssuedPersistenceResult(
                        code="CONFLICT",
                        timestamp_ms=_timestamp_ms(evidence.received_at),
                        reason_code="STATE_CONFLICT",
                    )
                raise RuntimeError("PickingTask 发布 evidence 处于非法应用状态")
            if evidence.id is None:
                raise RuntimeError("PickingTask 发布 evidence 缺少主键")
            await self._tasks.lock_task_identity(db, envelope.data.task_id)
            await self._tasks.lock_dispatch_sequence(db, envelope.data.dispatch_sequence)
            existing_task = await self._tasks.get_by_task_id_for_update(db, envelope.data.task_id)
            sequence_owner = await self._tasks.get_queued_by_dispatch_sequence_for_update(
                db,
                envelope.data.dispatch_sequence,
            )
            if existing_task is not None or sequence_owner is not None:
                evidence.apply_status = InboundEvidenceApplyStatus.RECONCILING
                evidence.processed_at = received_at
                return PickingTaskIssuedPersistenceResult(
                    code="CONFLICT",
                    timestamp_ms=_timestamp_ms(evidence.received_at),
                    reason_code="STATE_CONFLICT",
                )
            await self._tasks.add(
                db,
                PickingTask(
                    task_id=envelope.data.task_id,
                    task_type=envelope.data.task_type,
                    queue_revision=envelope.data.queue_revision,
                    dispatch_sequence=envelope.data.dispatch_sequence,
                    not_before_ms=envelope.data.not_before,
                    issued_at_ms=envelope.timestamp,
                    issued_evidence_id=evidence.id,
                ),
            )
            evidence.processed_at = received_at
            return PickingTaskIssuedPersistenceResult(
                code="RECEIVED",
                timestamp_ms=_timestamp_ms(evidence.received_at),
            )


def _timestamp_ms(value: datetime) -> int:
    return int(timezone.to_utc(value).timestamp() * 1000)


__all__ = [
    "PickingTaskIssuedService",
]
