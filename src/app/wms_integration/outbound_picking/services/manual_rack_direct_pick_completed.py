"""退料货架直接取料完成事件的本地应用；本地状态经 InboundEvidence 同事务落地。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.app.execution.models import InboundEvidenceApplyStatus, InboundEvidenceKind
from src.app.execution.services import InboundEvidenceConflictResult, InboundEvidenceService
from src.app.wms_adapter.outbound_picking.manual_rack_direct_pick_event_handler import (
    ManualRackDirectPickPersistenceResult,
)
from src.app.wms_adapter.outbound_picking.manual_rack_direct_pick_wire import ManualRackDirectPickInvalidData
from src.app.wms_integration.outbound_picking.repositories.picking_task_repository import PickingTaskRepository
from src.app.wms_integration.outbound_picking.repositories.plan_delta_repository import PickingTaskPlanDeltaRepository
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.wms_adapter.outbound_picking.manual_rack_direct_pick_wire import ManualRackDirectPickEvent


class ManualRackDirectPickCompletedService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        evidence_service: InboundEvidenceService | None = None,
        picking_tasks: PickingTaskRepository | None = None,
        plan_repository: PickingTaskPlanDeltaRepository | None = None,
    ) -> None:
        self._sessions = session_factory
        self._evidence = evidence_service or InboundEvidenceService()
        self._tasks = picking_tasks or PickingTaskRepository()
        self._plans = plan_repository or PickingTaskPlanDeltaRepository()

    async def record(
        self,
        envelope: ManualRackDirectPickEvent | ManualRackDirectPickInvalidData,
        *,
        received_at: datetime,
    ) -> ManualRackDirectPickPersistenceResult:
        invalid = isinstance(envelope, ManualRackDirectPickInvalidData)
        payload = envelope.raw_envelope if invalid else envelope.model_dump(mode="json")
        operation, operation_id = payload["operation"], payload["operation_id"]
        async with self._sessions.begin() as db:
            task = await self._tasks.get_by_task_id_for_update(db, envelope.data.task_id) if not invalid else None
            task_id = task.id if task is not None else None
            bound = task_id is not None and await self._plans.has_active_direct_pick_face(
                db,
                picking_task_id=task_id,
                rack_id=envelope.data.rack_id,
                rack_face=envelope.data.rack_face,
            )
            # picking_task_id 必须在同一事务内随 accept() 一次调用直接绑定；
            # 严禁 accept() 返回后再回填 evidence.picking_task_id（e47772cd/f93be730 教训）。
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
                apply_status=(
                    InboundEvidenceApplyStatus.IGNORED
                    if invalid
                    else InboundEvidenceApplyStatus.APPLIED
                    if bound
                    else InboundEvidenceApplyStatus.RECONCILING
                ),
            )
            evidence = acceptance.evidence
            timestamp_ms = int(timezone.to_utc(evidence.received_at).timestamp() * 1000)
            if isinstance(acceptance, InboundEvidenceConflictResult):
                return ManualRackDirectPickPersistenceResult(
                    code="CONFLICT",
                    timestamp_ms=timestamp_ms,
                    reason_code="IDEMPOTENCY_CONFLICT",
                )
            if invalid:
                evidence.processed_at = evidence.received_at
                return ManualRackDirectPickPersistenceResult(
                    code="REJECTED",
                    timestamp_ms=timestamp_ms,
                    reason_code="INVALID_DATA",
                )
            if bound and task_id is not None and not acceptance.duplicate:
                already_completed = await self._plans.has_direct_pick_face_completion(
                    db,
                    picking_task_id=task_id,
                    rack_id=envelope.data.rack_id,
                    rack_face=envelope.data.rack_face,
                )
                if not already_completed:
                    await self._plans.add_direct_pick_face_completion(
                        db,
                        picking_task_id=task_id,
                        rack_id=envelope.data.rack_id,
                        rack_face=envelope.data.rack_face,
                        completed_at=timezone.to_utc(envelope.data.completed_at / 1000).replace(tzinfo=None),
                        source_evidence_id=evidence.id,
                    )
            return ManualRackDirectPickPersistenceResult(
                code="DUPLICATE" if acceptance.duplicate else "RECEIVED",
                timestamp_ms=timestamp_ms,
            )


__all__ = ["ManualRackDirectPickCompletedService"]
