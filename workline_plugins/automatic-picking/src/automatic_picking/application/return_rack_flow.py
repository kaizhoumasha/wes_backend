"""退料架到位、范围完成和换面离场；现有 batch 扫描恢复当前义务。"""

from datetime import timedelta

from sqlalchemy import select
from wes_plugin_sdk import (
    RackDepartureReady,
    RackDepartureWait,
    TransportRackPosition,
    TransportRcsTemplateId,
    wms_operations,
)

from src.app.execution.models import InboundEvidence, WmsConfirmation
from src.app.wms_integration.outbound_picking.models import PickingTask
from src.app.wms_integration.outbound_picking.services.rack_departure import RackDepartureResultReader
from src.app.wms_integration.return_rack_task_completion.reader import ReturnRackTaskCompletedReader
from src.app.wms_integration.return_rack_task_completion.repository import ReturnRackTaskCompletionRepository
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone

from .return_allocation_model import AutomaticPickingReturnAllocation
from .return_rack_repository import RETURN_RACK_OUT, RETURN_RACK_ROTATE, ReturnRackRepository


class AutomaticPickingReturnRackFlow:
    def __init__(self, creator, arrival_scheduler, departure_scheduler, *, clock=timezone.now_for_db):
        self.repository = ReturnRackRepository()
        self.creator, self.arrivals, self.departures = creator, arrival_scheduler, departure_scheduler
        self.reader, self.clock = RackDepartureResultReader(), clock

    async def advance_in_session(self, db, line):
        await self.repository.discover(db, line.id)
        count = 0
        for current in await self.repository.pending(db, line.id):
            # 原离场动作即使父任务完成或窗口已释放，仍独立等待原结果。
            if current.departure_transport_task_id is not None:
                fact = await self.repository.result(db, current, current.departure_transport_task_id)
                if fact is not None:
                    current.closed_evidence_id = fact.id
                    count += 1
                continue
            if current.rotation_transport_task_id is not None:
                fact = await self.repository.result(db, current, current.rotation_transport_task_id)
                if fact is None:
                    continue
                current.arrival_transport_task_id = current.rotation_transport_task_id
                current.rotation_transport_task_id = None
                current.arrival_confirmation_id = None
                count += 1
            context = await self.repository.current_arrival(db, current)
            if context is None:
                continue
            transport, _evidence, position = context
            task = await db.get(PickingTask, current.picking_task_id, with_for_update=True, populate_existing=True)
            if task is None or task.workline_id != line.id:
                raise ValueError("return rack lost task owner")
            if current.arrival_confirmation_id is None:
                operation_id = new_uuid7()
                await self.arrivals.create_in_session(
                    db,
                    wms_operations.outbound_return_rack_arrival_report(
                        operation_id=operation_id,
                        task_id=task.task_id,
                        transport_task_id=transport.transport_task_id,
                        outcome_revision=transport.outcome_version,
                        rack_id=current.rack_id,
                        final_position=TransportRackPosition(position.position_json["location_code"]),
                        arrival_face=position.arrival_face,
                    ),
                    picking_task_id=task.id,
                    created_at=self.clock(),
                )
                current.arrival_confirmation_id = await db.scalar(
                    select(WmsConfirmation.id).where(WmsConfirmation.operation_id == operation_id)
                )
                count += 1
            if not line.is_active or task.plan_blocked_evidence_id is not None:
                continue
            members = await self.repository.members(db, task.id, current.rack_id)
            if not members or await self.repository.open_pickups(db, task.id, current.rack_id, position.arrival_face):
                continue
            pending = [member for member in members if not await self.repository.member_done(db, member)]
            frozen_departure = current.departure_confirmation_id is not None
            if not frozen_departure and any(member.rack_face == position.arrival_face for member in pending):
                continue
            _, root = await self.repository.transport(db, current.root_transport_task_id)
            if (
                await db.scalar(
                    select(AutomaticPickingReturnAllocation.id)
                    .where(
                        AutomaticPickingReturnAllocation.workline_id == line.id,
                        AutomaticPickingReturnAllocation.rack_id == current.rack_id,
                        AutomaticPickingReturnAllocation.created_at >= root.created_at,
                        AutomaticPickingReturnAllocation.archived_at.is_(None),
                        AutomaticPickingReturnAllocation.settled_at.is_(None),
                    )
                    .limit(1)
                )
                is not None
            ):
                continue
            if frozen_departure:
                count += await self._departure(db, line, current, position)
            elif pending:
                member = pending[0]
                handle = await self.creator.create_rotate(
                    db,
                    workline_id=line.id,
                    picking_task_id=task.id,
                    source_evidence_id=member.source_evidence_id,
                    correlation_id=f"return-rack:{current.root_transport_task_id}:{member.id}",
                    step=RETURN_RACK_ROTATE,
                    rack_id=current.rack_id,
                    position=TransportRackPosition(position.position_json["location_code"]),
                    target_face=member.rack_face,
                )
                current.rotation_transport_task_id = handle.transport_task_id
                count += 1
            elif not await self.repository.open_pickups(db, task.id, current.rack_id):
                count += await self._departure(db, line, current, position)
        await db.flush()
        return count

    async def _request(self, db, line, current, position):
        operation_id = new_uuid7()
        await self.departures.create_in_session(
            db,
            wms_operations.outbound_rack_departure_decide(
                operation_id=operation_id,
                task_id=None,
                rack_id=current.rack_id,
                current_location=TransportRackPosition(position.position_json["location_code"]),
                current_face=position.arrival_face,
            ),
            workline_id=line.id,
            created_at=self.clock(),
        )
        current.departure_confirmation_id = await db.scalar(
            select(WmsConfirmation.id).where(WmsConfirmation.operation_id == operation_id)
        )
        current.departure_retry_at = None
        return 1

    async def _departure(self, db, line, current, position):
        if current.departure_confirmation_id is None:
            return await self._request(db, line, current, position)
        confirmation = await db.get(WmsConfirmation, current.departure_confirmation_id, populate_existing=True)
        if confirmation is None:
            raise ValueError("return rack lost current departure request")
        if confirmation.status != "COMPLETED":
            return 0
        evidence = await db.get(InboundEvidence, confirmation.response_evidence_id)
        if evidence is None or evidence.published_at is None:
            return 0
        snapshot = await self.reader.by_operation_id(
            db, workline_id=line.id, operation_id=confirmation.operation_id, rack_id=current.rack_id
        )
        if (
            snapshot.intent.current_location.location_code != position.position_json["location_code"]
            or snapshot.intent.current_face != position.arrival_face
        ):
            raise ValueError("return rack departure no longer matches current arrival")
        result = snapshot.outcome.result
        if type(result) is RackDepartureWait:
            due = evidence.received_at + timedelta(milliseconds=result.retry_after_ms)
            if current.departure_retry_at is not None and current.departure_retry_at != due:
                raise ValueError("return rack WAIT deadline drift")
            current.departure_retry_at = due
            if self.clock() >= due:
                return await self._request(db, line, current, position)
            return 0
        if type(result) is not RackDepartureReady:
            return 0
        handle = await self.creator.create_source_return(
            db,
            workline_id=line.id,
            source_evidence_id=evidence.id,
            correlation_id="departure:" + confirmation.operation_id,
            step=RETURN_RACK_OUT,
            rack_id=current.rack_id,
            destination=result.rack_destination,
            rcs_template_id=TransportRcsTemplateId.F01,
        )
        current.departure_transport_task_id = handle.transport_task_id
        current.arrival_transport_task_id = None
        current.arrival_confirmation_id = None
        return 1

    async def apply_decision_result_in_session(self, db, *, line, evidence):
        current = await self.repository.by_decision(db, line.id, evidence.operation_id)
        if current is None or current.closed_evidence_id is not None:
            return "RETURN_RACK_OLD_DECISION"
        snapshot = await self.reader.for_evidence(
            db,
            workline_id=line.id,
            operation_id=evidence.operation_id,
            rack_id=current.rack_id,
            evidence_id=evidence.id,
        )
        if type(snapshot.outcome.result) is RackDepartureWait:
            current.departure_retry_at = evidence.received_at + timedelta(
                milliseconds=snapshot.outcome.result.retry_after_ms
            )
        return "RETURN_RACK_DECIDED"

    async def apply_transport_result_in_session(self, db, *, line, evidence):
        from .return_rack_model import AutomaticPickingReturnRack
        from .return_rack_repository import RETURN_RACK_IN

        if evidence.normalized_payload.get("step") == RETURN_RACK_IN:
            return "RETURN_RACK_ARRIVED"
        current = await db.scalar(
            select(AutomaticPickingReturnRack).where(
                AutomaticPickingReturnRack.workline_id == line.id,
                (AutomaticPickingReturnRack.rotation_transport_task_id == evidence.transport_task_id)
                | (AutomaticPickingReturnRack.departure_transport_task_id == evidence.transport_task_id),
            )
        )
        if current is None:
            return "RETURN_RACK_OLD_RESULT"
        task, binding = await self.repository.transport(db, evidence.transport_task_id)
        if (
            binding.resource_fence_id != current.rack_id
            or binding.step != evidence.normalized_payload.get("step")
            or task.outcome_version != evidence.normalized_payload.get("outcome_version")
        ):
            raise ValueError("return rack result lost original identity")
        return "RETURN_RACK_RESULT"

    async def apply_scope_in_session(self, db, *, line, evidence):
        fact = ReturnRackTaskCompletedReader().read(evidence)
        if fact is None:
            return "IGNORED"
        task = await db.get(PickingTask, fact.picking_task_id, with_for_update=True, populate_existing=True)
        if task is None or task.task_id != fact.task_id or task.workline_id != line.id:
            return "RECONCILING"
        if task.last_applied_plan_revision < fact.through_plan_revision:
            return "WAIT_FOR_EXIT"
        completions = ReturnRackTaskCompletionRepository()
        current = await completions.get(db, task.id, fact.rack_id)
        if current is not None and current.through_plan_revision >= fact.through_plan_revision:
            return "RETURN_RACK_SCOPE_DONE"
        if not any(rev <= fact.through_plan_revision for rev in await completions.members(db, task.id, fact.rack_id)):
            return "RECONCILING"
        await completions.add(db, fact)
        return "RETURN_RACK_SCOPE_DONE"
