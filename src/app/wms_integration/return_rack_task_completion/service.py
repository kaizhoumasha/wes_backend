"""整架业务完成通知的可靠接收；后续业务由冻结工作线的插件应用。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.app.execution.models import InboundEvidenceApplyStatus, InboundEvidenceKind
from src.app.execution.services import InboundEvidenceConflictResult, InboundEvidenceService
from src.app.wms_adapter.return_rack_task_completion.handler import (
    ReturnRackTaskCompletedPersistenceResult,
)
from src.app.wms_adapter.return_rack_task_completion.wire import ReturnRackTaskCompletedInvalidData
from src.app.wms_integration.outbound_picking.repositories.picking_task_repository import PickingTaskRepository
from src.app.workline.repositories import workline_repository
from src.core.transaction_wakeup import defer_wakeup
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.wms_adapter.return_rack_task_completion.wire import ReturnRackTaskCompletedEvent
    from src.core.task_queue_gateway import TaskQueueGateway


class ReturnRackTaskCompletedService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        evidence_service: InboundEvidenceService | None = None,
        picking_tasks: PickingTaskRepository | None = None,
        task_queue_gateway: TaskQueueGateway | None = None,
    ) -> None:
        self._sessions = session_factory
        self._evidence = evidence_service or InboundEvidenceService()
        self._tasks = picking_tasks or PickingTaskRepository()
        self._queue = task_queue_gateway

    async def record(
        self,
        envelope: ReturnRackTaskCompletedEvent | ReturnRackTaskCompletedInvalidData,
        *,
        received_at: datetime,
    ) -> ReturnRackTaskCompletedPersistenceResult:
        invalid = isinstance(envelope, ReturnRackTaskCompletedInvalidData)
        payload = envelope.raw_envelope if invalid else envelope.model_dump(mode="json")
        operation, operation_id = payload["operation"], payload["operation_id"]
        async with self._sessions.begin() as db:
            task = None
            if not invalid:
                workline_id = await self._tasks.get_workline_id_by_task_id(db, envelope.data.task_id)
                if workline_id is not None:
                    await workline_repository.get_for_authority_update(db, workline_id)
                    task = await self._tasks.get_by_task_id_for_update(db, envelope.data.task_id)
                if task is None:
                    # 未发布任务没有可靠应用 Owner；沿原身份重试，不能先 ACK 再永久搁置。
                    raise RuntimeError("PickingTask 尚未发布，无法可靠接收整架完成通知")
            task_id = task.id if task is not None else None
            acceptance = await self._evidence.accept(
                db,
                kind=InboundEvidenceKind.WMS_EVENT,
                source_identity=f"{operation}:{operation_id}",
                normalized_payload=payload,
                received_at=received_at,
                workline_id=task.workline_id if task is not None else None,
                picking_task_id=task_id,
                contract_key=operation,
                contract_version="1.0",
                operation=operation,
                operation_id=operation_id,
                apply_status=(InboundEvidenceApplyStatus.IGNORED if invalid else InboundEvidenceApplyStatus.APPLIED),
            )
            evidence = acceptance.evidence
            timestamp_ms = int(timezone.to_utc(evidence.received_at).timestamp() * 1000)
            if isinstance(acceptance, InboundEvidenceConflictResult):
                return ReturnRackTaskCompletedPersistenceResult(
                    code="CONFLICT",
                    timestamp_ms=timestamp_ms,
                    reason_code="IDEMPOTENCY_CONFLICT",
                )
            if invalid:
                evidence.processed_at = evidence.received_at
                return ReturnRackTaskCompletedPersistenceResult(
                    code="REJECTED",
                    timestamp_ms=timestamp_ms,
                    reason_code="INVALID_DATA",
                )
            # 提交后唤醒；ACK 不代替后续面级事实核对。
            if (
                self._queue is not None
                and evidence.workline_id is not None
                and evidence.apply_status == InboundEvidenceApplyStatus.APPLIED
            ):
                defer_wakeup(db, self._queue.enqueue_execution_facts)
            return ReturnRackTaskCompletedPersistenceResult(
                code="DUPLICATE" if acceptance.duplicate else "RECEIVED",
                timestamp_ms=timestamp_ms,
            )


__all__ = ["ReturnRackTaskCompletedService"]
