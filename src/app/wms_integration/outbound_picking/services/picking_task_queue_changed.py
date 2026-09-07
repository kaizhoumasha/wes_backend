"""在共享 Evidence 事务内应用 PickingTask 队列版本。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.app.execution.models import InboundEvidenceApplyStatus as Status
from src.app.execution.models import InboundEvidenceKind
from src.app.execution.services import InboundEvidenceConflictResult, InboundEvidenceService
from src.app.wms_adapter.outbound_picking.queue_changed_event_handler import (
    PickingTaskQueueChangedPersistenceResult as Result,
)
from src.app.wms_adapter.outbound_picking.queue_changed_wire import (
    PickingTaskQueueChangedEvent,
    PickingTaskQueueChangedInvalidData,
)
from src.app.wms_integration.outbound_picking.models import PickingTaskStatus
from src.app.wms_integration.outbound_picking.repositories import PickingTaskRepository, picking_task_repository
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.execution.models import InboundEvidence


class PickingTaskQueueChangedService:
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
        envelope: PickingTaskQueueChangedEvent | PickingTaskQueueChangedInvalidData,
        *,
        received_at: datetime,
    ) -> Result:
        invalid = isinstance(envelope, PickingTaskQueueChangedInvalidData)
        payload = envelope.raw_envelope if invalid else envelope.model_dump(mode="json", exclude_none=True)
        operation, operation_id = payload["operation"], payload["operation_id"]
        async with self._sessions.begin() as db:
            acceptance = await self._evidence.accept(
                db,
                kind=InboundEvidenceKind.WMS_EVENT,
                source_identity=f"{operation}:{operation_id}",
                normalized_payload=payload,
                received_at=received_at,
                operation=operation,
                operation_id=operation_id,
                contract_key=operation,
                contract_version="1.0",
                apply_status=Status.IGNORED if invalid else Status.PENDING,
            )
            evidence = acceptance.evidence
            if isinstance(acceptance, InboundEvidenceConflictResult):
                return self._result(evidence, "CONFLICT", "IDEMPOTENCY_CONFLICT")
            if isinstance(envelope, PickingTaskQueueChangedInvalidData):
                evidence.processed_at = evidence.received_at
                return self._result(evidence, "REJECTED", "INVALID_DATA")
            if acceptance.duplicate:
                if evidence.apply_status == Status.APPLIED:
                    return self._result(evidence, "DUPLICATE")
                if evidence.apply_status == Status.RECONCILING:
                    reason = await self._tasks.first_queue_rejection(db, evidence.id)
                    if reason is None:
                        raise RuntimeError("队列拒绝缺少首次原因")
                    return self._result(evidence, "CONFLICT", reason)
                raise RuntimeError("队列更新 Evidence 处于非法状态")
            data = envelope.data
            # 与 issued 共用任务身份和目标优先序锁；prepare 的领取由同一任务行锁串行化。
            await self._tasks.lock_task_identity(db, data.task_id)
            if data.dispatch_sequence is not None:
                await self._tasks.lock_dispatch_sequence(db, data.dispatch_sequence)
            task = await self._tasks.get_by_task_id_for_update(db, data.task_id)
            reason = None
            if task is None:
                reason = "REFERENCE_CONFLICT"
            elif task.status != PickingTaskStatus.QUEUED or task.plan_blocked_evidence_id is not None:
                reason = "STATE_CONFLICT"
            elif data.queue_revision != task.queue_revision + 1:
                reason = "REVISION_CONFLICT"
            elif (
                (data.dispatch_sequence is None or data.dispatch_sequence == task.dispatch_sequence)
                and (data.not_before is None or data.not_before == task.not_before_ms)
            ) or (
                data.dispatch_sequence is not None
                and await self._tasks.queued_sequence_is_occupied(db, data.dispatch_sequence, excluding_task_id=task.id)
            ):
                reason = "STATE_CONFLICT"
            if reason is not None:
                await self._evidence.record_conflict(
                    db,
                    first=evidence,
                    source_identity=evidence.source_identity,
                    normalized_payload=evidence.normalized_payload,
                    reason_code=reason,
                    received_at=received_at,
                )
                evidence.apply_status = Status.RECONCILING
                evidence.processed_at = received_at
                return self._result(evidence, "CONFLICT", reason)
            if task is None:
                raise RuntimeError("队列更新缺少任务")
            if data.dispatch_sequence is not None:
                task.dispatch_sequence = data.dispatch_sequence
            if data.not_before is not None:
                task.not_before_ms = data.not_before
            task.queue_revision = data.queue_revision
            task.increment_version()
            evidence.apply_status = Status.APPLIED
            evidence.processed_at = received_at
            await self._tasks.flush(db)
            return self._result(evidence, "RECEIVED")

    @staticmethod
    def _result(evidence: InboundEvidence, code: str, reason: str | None = None) -> Result:
        return Result(
            code=code, timestamp_ms=int(timezone.to_utc(evidence.received_at).timestamp() * 1000), reason_code=reason
        )


__all__ = ["PickingTaskQueueChangedService"]
