"""正常任务完成确认独立于退箱、换面离场和 drain 清场。"""

from datetime import timedelta

from sqlalchemy import or_, select
from wes_plugin_sdk import (
    PickingTaskBusinessInProgress,
    PickingTaskCompleted,
    PickingTaskPlanRevisionStale,
    wms_operations,
)

from src.app.execution.models import InboundEvidence, MaterialExecution, WmsConfirmation
from src.app.wms_integration.outbound_picking.models import DirectPickExecution, PickingTaskBinSourceRack
from src.app.wms_integration.outbound_picking.services.picking_task_completion import PickingTaskCompletionResultReader
from src.app.wms_integration.outbound_picking.services.picking_task_prepare import PickingTaskPrepareResultReader
from src.app.wms_integration.outbound_picking.services.return_rack_arrival import ReturnRackArrivalResultReader
from src.core.uuid7 import new_uuid7
from src.utils.canonical_json import canonical_json_digest
from src.utils.timezone import timezone

from .feed_model import AutomaticPickingFeed
from .pickup_model import AutomaticPickingDirectPick, AutomaticPickingPickup
from .return_rack_model import AutomaticPickingReturnRack, AutomaticPickingTaskCompletion
from .return_rack_repository import member_done_clause
from .work_model import AutomaticPickingWork


class AutomaticPickingCompletionFlow:
    def __init__(self, scheduler, *, first_plan_timeout_ms=None, clock=timezone.now_for_db):
        if first_plan_timeout_ms is not None and (type(first_plan_timeout_ms) is not int or first_plan_timeout_ms <= 0):
            raise ValueError("first plan timeout requires explicit positive milliseconds")
        self.scheduler, self.clock = scheduler, clock
        self.reader = PickingTaskCompletionResultReader()
        self.first_plan_timeout_ms = first_plan_timeout_ms
        self.prepare_reader = PickingTaskPrepareResultReader()

    async def ready(self, db, task):
        if (
            await db.scalar(
                select(DirectPickExecution.id)
                .where(DirectPickExecution.picking_task_id == task.id, ~member_done_clause(DirectPickExecution))
                .limit(1)
            )
            is not None
        ):
            return False
        if (
            await db.scalar(
                select(AutomaticPickingPickup.id)
                .outerjoin(
                    AutomaticPickingDirectPick, AutomaticPickingDirectPick.id == AutomaticPickingPickup.direct_pick_id
                )
                .outerjoin(AutomaticPickingWork, AutomaticPickingWork.id == AutomaticPickingPickup.work_id)
                .outerjoin(MaterialExecution, MaterialExecution.id == AutomaticPickingPickup.material_execution_id)
                .where(
                    or_(
                        AutomaticPickingDirectPick.picking_task_id == task.id,
                        AutomaticPickingWork.picking_task_id == task.id,
                    ),
                    or_(MaterialExecution.id.is_(None), MaterialExecution.status != "CLOSED"),
                )
                .limit(1)
            )
            is not None
        ):
            return False
        if (
            await db.scalar(
                select(AutomaticPickingWork.id)
                .where(
                    AutomaticPickingWork.picking_task_id == task.id,
                    AutomaticPickingWork.archived_at.is_(None),
                    AutomaticPickingWork.phase.not_in(("EXIT_PENDING", "CLOSED")),
                )
                .limit(1)
            )
            is not None
        ):
            return False
        feeds = (
            await db.scalars(select(AutomaticPickingFeed).where(AutomaticPickingFeed.picking_task_id == task.id))
        ).all()
        completed = {member_id for feed in feeds for member_id in feed.completed_source_member_ids}
        completed.update(feed.source_member_id for feed in feeds if feed.phase == "FEED_COMPLETE")
        for member in (
            await db.scalars(
                select(PickingTaskBinSourceRack).where(PickingTaskBinSourceRack.picking_task_id == task.id)
            )
        ).all():
            if member.cancelled_evidence_id is None and member.id not in completed:
                return False
        # 当前仍需报告的到位必须先建立原义务；已确认旧报告允许清理，不作为运行时历史依赖。
        for rack in (
            await db.scalars(
                select(AutomaticPickingReturnRack).where(AutomaticPickingReturnRack.picking_task_id == task.id)
            )
        ).all():
            if rack.arrival_transport_task_id is not None and rack.arrival_confirmation_id is None:
                return False
        confirmations = (
            await db.scalars(
                select(WmsConfirmation).where(
                    WmsConfirmation.picking_task_id == task.id,
                    WmsConfirmation.operation == "outbound.return_rack.arrival_report@v1",
                )
            )
        ).all()
        for confirmation in confirmations:
            snapshot = await ReturnRackArrivalResultReader().by_id(
                db, picking_task_id=task.id, confirmation_id=confirmation.id
            )
            evidence = (
                await db.get(InboundEvidence, confirmation.response_evidence_id)
                if confirmation.response_evidence_id
                else None
            )
            if (
                snapshot.intent.task_id != task.task_id
                or canonical_json_digest(confirmation.request_payload) != confirmation.request_digest
            ):
                raise ValueError("arrival report lost frozen task identity")
            if evidence is not None and (
                evidence.picking_task_id != task.id
                or canonical_json_digest(evidence.normalized_payload) != evidence.payload_digest
            ):
                raise ValueError("arrival response owner or digest mismatch")
            if (
                snapshot is None
                or snapshot.status != "COMPLETED"
                or confirmation.response_result not in ("RECORDED", "DUPLICATE")
                or evidence is None
            ):
                return False
        return True

    async def current(self, db, task):
        return await db.scalar(
            select(AutomaticPickingTaskCompletion)
            .where(AutomaticPickingTaskCompletion.picking_task_id == task.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def result(self, db, task, current, *, evidence_id=None):
        confirmation = await db.get(WmsConfirmation, current.confirmation_id, populate_existing=True)
        if current.workline_id != task.workline_id:
            raise ValueError("completion current owner mismatch")
        if confirmation is None or canonical_json_digest(confirmation.request_payload) != confirmation.request_digest:
            raise ValueError("completion lost frozen current request")
        snapshot = await self.reader.by_id(db, picking_task_id=task.id, confirmation_id=confirmation.id)
        if snapshot.task_id != task.task_id:
            raise ValueError("completion task identity differs")
        evidence = (
            await db.get(InboundEvidence, confirmation.response_evidence_id)
            if confirmation.response_evidence_id
            else None
        )
        if evidence_id is not None and (evidence is None or evidence.id != evidence_id):
            raise ValueError("completion current response identity differs")
        if evidence is not None and (
            evidence.picking_task_id != task.id
            or canonical_json_digest(evidence.normalized_payload) != evidence.payload_digest
        ):
            raise ValueError("completion response owner or digest differs")
        return snapshot, evidence

    async def advance_in_session(self, db, line, task, *, now=None):  # noqa: PLR0911
        now = now or self.clock()
        if (
            not line.is_active
            or task.workline_id != line.id
            or task.status not in ("PREPARING", "EXECUTING")
            or task.plan_blocked_evidence_id is not None
            or (task.last_applied_plan_revision == 0 and task.status != "PREPARING")
        ):
            return 0
        current = await self.current(db, task)
        if task.last_applied_plan_revision == 0:
            if current is None:
                if self.first_plan_timeout_ms is None:
                    return 0
                accepted_at = await self.prepare_reader.accepted_at_in_session(
                    db, picking_task_id=task.id, task_id=task.task_id, workline_code=line.line_code
                )
                if accepted_at is None:
                    return 0
                current = AutomaticPickingTaskCompletion(
                    workline_id=line.id,
                    picking_task_id=task.id,
                    first_plan_due_at=accepted_at + timedelta(milliseconds=self.first_plan_timeout_ms),
                )
                db.add(current)
                await db.flush()
                if now < current.first_plan_due_at:
                    return 1
            if current.workline_id != line.id:
                raise ValueError("first plan current owner mismatch")
            if current.confirmation_id is None and now < current.first_plan_due_at:
                return 0
        if current is not None and current.confirmation_id is not None:
            snapshot, evidence = await self.result(db, task, current)
            if snapshot.status != "COMPLETED" or evidence is None or snapshot.outcome is None:
                return 0
            result = snapshot.outcome.result
            if type(result) is PickingTaskCompleted and snapshot.plan_revision == task.last_applied_plan_revision:
                if await self.ready(db, task):
                    task.status = "EXECUTION_COMPLETED"
                    return 1
                return 0
            if type(result) is PickingTaskPlanRevisionStale:
                if task.last_applied_plan_revision < result.current_plan_revision:
                    return 0
            elif type(result) is PickingTaskBusinessInProgress:
                if now < evidence.received_at + timedelta(milliseconds=result.retry_after_ms):
                    return 0
            elif type(result) is not PickingTaskCompleted:
                return 0
        if not await self.ready(db, task):
            return 0
        operation_id = new_uuid7()
        await self.scheduler.create_in_session(
            db,
            wms_operations.outbound_picking_task_completion_confirm(
                operation_id=operation_id,
                task_id=task.task_id,
                last_applied_plan_revision=task.last_applied_plan_revision,
            ),
            picking_task_id=task.id,
            created_at=now,
        )
        confirmation_id = await db.scalar(
            select(WmsConfirmation.id).where(WmsConfirmation.operation_id == operation_id)
        )
        if current is None:
            db.add(
                AutomaticPickingTaskCompletion(
                    workline_id=line.id, picking_task_id=task.id, confirmation_id=confirmation_id
                )
            )
        else:
            current.confirmation_id = confirmation_id
        await db.flush()
        return 1
