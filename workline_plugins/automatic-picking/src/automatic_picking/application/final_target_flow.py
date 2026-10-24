"""任务完成与最终转运架清场分离；batch 恢复原请求和原 F01。"""

from datetime import timedelta

from sqlalchemy import select
from wes_plugin_sdk import RackDepartureReady, RackDepartureWait, wms_operations

from src.app.execution.models import InboundEvidence, WmsConfirmation
from src.app.wms_integration.outbound_picking.models import PickingTask
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone

from .final_target_repository import FINAL_TARGET_OUT, FinalTargetRepository


class AutomaticPickingFinalTargetFlow:
    def __init__(self, creator, scheduler, *, clock=timezone.now_for_db):
        self.creator, self.scheduler, self.clock = creator, scheduler, clock
        self.repository = FinalTargetRepository()

    async def advance_in_session(self, db, line):
        await self.repository.discover(db, line.id)
        count = 0
        for current in await self.repository.pending(db, line.id):
            count += await self._advance(db, line, current)
        return count

    async def _advance(self, db, line, current):  # noqa: PLR0911
        if current.departure_transport_task_id is not None:
            original, binding = await self.repository.transports.transport(db, current.departure_transport_task_id)
            await self.repository.validate_transport(
                db, original.transport_task_id, original.client_request_id, binding
            )
            fact = await self.repository.transports.result(db, current, current.departure_transport_task_id)
            if fact is not None:
                current.closed_evidence_id = fact.id
                return 1
            return 0
        if not line.is_active:
            return 0
        task = await db.get(PickingTask, current.picking_task_id, populate_existing=True)
        if task is None or task.workline_id != line.id:
            raise ValueError("final target lost original task")
        arrival = await self.repository.arrival(db, line, current)
        if arrival is None:
            return 0
        location, face, arrival_fact = arrival
        now = self.clock()
        if current.departure_confirmation_id is not None:
            snapshot = await self.repository.decision(db, current)
            if snapshot.intent.task_id != task.task_id or snapshot.intent.rack_id != current.rack_id:
                raise ValueError("final target current task/rack differs")
            if snapshot.status != "COMPLETED" or snapshot.outcome is None:
                return 0
            result = snapshot.outcome.result
            if type(result) is RackDepartureWait:
                evidence = await db.get(InboundEvidence, snapshot.evidence_id)
                if current.departure_retry_at is None:
                    current.departure_retry_at = evidence.received_at + timedelta(milliseconds=result.retry_after_ms)
                if now < current.departure_retry_at:
                    return 0
            elif type(result) is RackDepartureReady:
                confirmation = await db.get(WmsConfirmation, current.departure_confirmation_id)
                if (
                    snapshot.intent.current_location != location
                    or snapshot.intent.current_face != face
                    or confirmation.created_at < arrival_fact.received_at
                    or result.rack_destination == location
                ):
                    return 0
                handle = await self.creator.create_transfer_departure(
                    db,
                    workline_id=line.id,
                    picking_task_id=task.id,
                    source_evidence_id=snapshot.evidence_id,
                    operation_id=snapshot.intent.operation_id,
                    step=FINAL_TARGET_OUT,
                    rack_id=current.rack_id,
                    destination=result.rack_destination,
                )
                current.departure_transport_task_id = handle.transport_task_id
                # 原 F01 已独立持有冻结 READY；到位根的可清理历史不再参与恢复。
                current.arrival_transport_task_id = None
                await db.flush()
                return 1
            else:
                return 0
        # 该持久业务事实由 completion driver 应用 WMS 确认时原子落地；不重读已闭合确认历史。
        elif task.status != "EXECUTION_COMPLETED":
            return 0
        operation_id = new_uuid7()
        await self.scheduler.create_in_session(
            db,
            wms_operations.outbound_rack_departure_decide(
                operation_id=operation_id,
                task_id=task.task_id,
                rack_id=current.rack_id,
                current_location=location,
                current_face=face,
            ),
            picking_task_id=task.id,
            created_at=now,
        )
        current.departure_confirmation_id = await db.scalar(
            select(WmsConfirmation.id).where(
                WmsConfirmation.operation == "outbound.rack.departure_decide@v1",
                WmsConfirmation.operation_id == operation_id,
            )
        )
        current.departure_retry_at = None
        await db.flush()
        return 1

    async def apply_transport_result_in_session(self, db, *, line, evidence):
        task, binding = await self.repository.transports.transport(db, evidence.transport_task_id)
        await self.repository.validate_transport(
            db, task.transport_task_id, evidence.normalized_payload.get("client_request_id"), binding
        )
        if (
            binding.workline_id != line.id
            or evidence.normalized_payload.get("outcome_version") != task.outcome_version
            or evidence.normalized_payload.get("status") != task.status
        ):
            raise ValueError("final target result version differs")
        return "APPLIED"
