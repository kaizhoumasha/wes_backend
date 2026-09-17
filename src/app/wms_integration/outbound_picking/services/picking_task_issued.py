"""PickingTask 发布事件的原子 evidence 与业务持久化。"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from src.app.execution.models import InboundEvidenceApplyStatus, InboundEvidenceKind
from src.app.execution.services import InboundEvidenceConflictResult, InboundEvidenceService
from src.app.wms_adapter.outbound_picking.event_handler import PickingTaskIssuedPersistenceResult
from src.app.wms_adapter.outbound_picking.wire import PickingTaskIssuedInvalidData
from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskType
from src.app.wms_integration.outbound_picking.repositories import PickingTaskRepository, picking_task_repository
from src.app.workline.repositories import WorkLineRepository
from src.app.workline.repositories import workline_repository as default_workline_repository
from src.core.transaction_wakeup import defer_wakeup
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.wms_adapter.outbound_picking.wire import PickingTaskIssuedEvent
    from src.core.task_queue_gateway import TaskQueueGateway


class PickingTaskIssuedService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        evidence_service: InboundEvidenceService | None = None,
        task_repository: PickingTaskRepository | None = None,
        prepare_plugin_identities: tuple[tuple[str, str], ...] = (),
        task_queue_gateway: TaskQueueGateway | None = None,
        workline_repository: WorkLineRepository | None = None,
    ) -> None:
        self._sessions = session_factory
        self._evidence = evidence_service or InboundEvidenceService()
        self._tasks = task_repository or picking_task_repository
        self._prepare_plugin_identities = prepare_plugin_identities
        self._task_queue = task_queue_gateway
        self._worklines = workline_repository or default_workline_repository

    async def record(
        self,
        envelope: PickingTaskIssuedEvent | PickingTaskIssuedInvalidData,
        *,
        received_at: datetime,
    ) -> PickingTaskIssuedPersistenceResult:
        invalid = isinstance(envelope, PickingTaskIssuedInvalidData)
        payload = (
            envelope.raw_envelope
            if isinstance(envelope, PickingTaskIssuedInvalidData)
            else envelope.model_dump(mode="json", exclude_none=True)
        )
        operation, operation_id = payload["operation"], payload["operation_id"]
        source_identity = f"{operation}:{operation_id}"
        async with self._sessions.begin() as db:
            acceptance = await self._evidence.accept(
                db,
                kind=InboundEvidenceKind.WMS_EVENT,
                source_identity=source_identity,
                normalized_payload=payload,
                received_at=received_at,
                contract_key=operation,
                contract_version="1.0",
                operation=operation,
                operation_id=operation_id,
                apply_status=InboundEvidenceApplyStatus.IGNORED if invalid else InboundEvidenceApplyStatus.APPLIED,
            )
            if isinstance(acceptance, InboundEvidenceConflictResult):
                return PickingTaskIssuedPersistenceResult(
                    code="CONFLICT",
                    timestamp_ms=_timestamp_ms(acceptance.evidence.received_at),
                    reason_code="IDEMPOTENCY_CONFLICT",
                )
            evidence = acceptance.evidence
            if isinstance(envelope, PickingTaskIssuedInvalidData):
                evidence.processed_at = evidence.received_at
                return PickingTaskIssuedPersistenceResult(
                    code="REJECTED",
                    timestamp_ms=_timestamp_ms(evidence.received_at),
                    reason_code="INVALID_DATA",
                )
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
            workline = await self._worklines.get_by_line_code_for_update(db, envelope.data.workline_code)
            if workline is None:
                evidence.apply_status = InboundEvidenceApplyStatus.RECONCILING
                evidence.processed_at = received_at
                return PickingTaskIssuedPersistenceResult(
                    code="CONFLICT",
                    timestamp_ms=_timestamp_ms(evidence.received_at),
                    reason_code="REFERENCE_CONFLICT",
                )
            plugin_identity = (workline.plugin_key, workline.plugin_version)
            line_type = workline.line_type.value if hasattr(workline.line_type, "value") else workline.line_type
            if (
                workline.is_active is not True
                or line_type not in (envelope.data.task_type, "HYBRID")
                or plugin_identity not in self._prepare_plugin_identities
            ):
                evidence.apply_status = InboundEvidenceApplyStatus.RECONCILING
                evidence.processed_at = received_at
                return PickingTaskIssuedPersistenceResult(
                    code="CONFLICT",
                    timestamp_ms=_timestamp_ms(evidence.received_at),
                    reason_code="STATE_CONFLICT",
                )
            workline_id = workline.id
            if not isinstance(workline_id, int) or workline_id <= 0:
                raise RuntimeError("PickingTask 指定 WorkLine 缺少持久身份")
            task = await self._tasks.add(
                db,
                PickingTask(
                    task_id=envelope.data.task_id,
                    task_type=cast("PickingTaskType", envelope.data.task_type),
                    queue_revision=envelope.data.queue_revision,
                    dispatch_sequence=envelope.data.dispatch_sequence,
                    not_before_ms=envelope.data.not_before,
                    issued_at_ms=envelope.timestamp,
                    issued_evidence_id=evidence.id,
                    workline_id=workline_id,
                ),
            )
            if task.id is None:
                raise RuntimeError("PickingTask 发布后缺少主键")
            evidence.picking_task_id = task.id
            evidence.processed_at = received_at
            await self._tasks.flush(db)
            if self._task_queue is not None:
                defer_wakeup(db, self._task_queue.enqueue_picking_task_prepare)
            return PickingTaskIssuedPersistenceResult(
                code="RECEIVED",
                timestamp_ms=_timestamp_ms(evidence.received_at),
            )


def _timestamp_ms(value: datetime) -> int:
    return int(timezone.to_utc(value).timestamp() * 1000)


__all__ = [
    "PickingTaskIssuedService",
]
