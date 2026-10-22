"""自动退料成员完成谓词与本架当前因果位置；不把投影当物理资源主账。"""

from sqlalchemy import exists, or_, select
from wes_plugin_sdk import RackDepartureReady, TransportRackPosition

from src.app.execution.models import (
    InboundEvidence,
    MaterialExecution,
    PositionProjection,
    TransportDecisionBinding,
    WmsConfirmation,
)
from src.app.transport.models import TransportTask
from src.app.wms_integration.outbound_picking.models import DirectPickExecution, DirectPickFaceCompletion
from src.app.wms_integration.outbound_picking.services.rack_departure import RackDepartureResultReader
from src.app.wms_integration.return_rack_task_completion.models import ReturnRackTaskCompletion
from src.utils.canonical_json import canonical_json_digest

from .pickup_model import AutomaticPickingDirectPick, AutomaticPickingPickup
from .return_rack_model import AutomaticPickingReturnRack

RETURN_RACK_IN = "PICKING_TASK_RETURN_RACK_IN"
RETURN_RACK_ROTATE = "AUTOMATIC_PICKING_RETURN_RACK_ROTATE"
RETURN_RACK_OUT = "AUTOMATIC_PICKING_RETURN_RACK_OUT"


def member_done_clause(member):
    return or_(
        member.cancelled_evidence_id.is_not(None),
        exists()
        .where(
            AutomaticPickingDirectPick.member_id == member.id,
            AutomaticPickingDirectPick.completed_evidence_id.is_not(None),
        )
        .correlate(member),
        exists()
        .where(
            DirectPickFaceCompletion.picking_task_id == member.picking_task_id,
            DirectPickFaceCompletion.plan_revision == member.plan_revision,
            DirectPickFaceCompletion.rack_id == member.rack_id,
            DirectPickFaceCompletion.rack_face == member.rack_face,
        )
        .correlate(member),
        exists()
        .where(
            ReturnRackTaskCompletion.picking_task_id == member.picking_task_id,
            ReturnRackTaskCompletion.rack_id == member.rack_id,
            ReturnRackTaskCompletion.through_plan_revision >= member.plan_revision,
        )
        .correlate(member),
    )


class ReturnRackRepository:
    async def member_admissible(self, db, line_id, member):
        currents = (
            await db.scalars(
                select(AutomaticPickingReturnRack).where(
                    AutomaticPickingReturnRack.workline_id == line_id,
                    AutomaticPickingReturnRack.rack_id == member.rack_id,
                    AutomaticPickingReturnRack.closed_evidence_id.is_(None),
                )
            )
        ).all()
        if not any(current.departure_confirmation_id is not None for current in currents):
            return True
        # 冻结离场不为未来版本续停；新轮次已有精确到位时可独立接管。
        for current in currents:
            if (
                current.picking_task_id == member.picking_task_id
                and current.departure_confirmation_id is None
                and current.departure_transport_task_id is None
                and current.rotation_transport_task_id is None
            ):
                arrival = await self.current_arrival(db, current)
                if arrival is not None and arrival[2].arrival_face == member.rack_face:
                    return True
        return False

    async def validate_outcome(self, db, outcome, binding):
        task, original = await self.transport(db, outcome.transport_task_id)
        if (
            original.id != binding.id
            or task.client_request_id != outcome.client_request_id
            or task.kind != "RACK_MOVE"
            or task.authority_workline_id != binding.workline_id
            or binding.picking_task_id is not None
            or binding.step != RETURN_RACK_OUT
            or not binding.correlation_id.startswith("departure:")
        ):
            raise ValueError("return departure lost original WorkLine action")
        snapshot = await RackDepartureResultReader().by_operation_id(
            db,
            workline_id=binding.workline_id,
            operation_id=binding.correlation_id.removeprefix("departure:"),
            rack_id=binding.resource_fence_id,
        )
        if (
            snapshot is None
            or snapshot.status != "COMPLETED"
            or type(snapshot.outcome.result) is not RackDepartureReady
            or snapshot.evidence_id != binding.source_evidence_id
        ):
            raise ValueError("return departure lost exact published READY")
        evidence = await db.get(InboundEvidence, snapshot.evidence_id)
        if (
            evidence.workline_id != binding.workline_id
            or evidence.picking_task_id is not None
            or canonical_json_digest(evidence.normalized_payload) != evidence.payload_digest
        ):
            raise ValueError("return departure READY evidence identity mismatch")
        destination = snapshot.outcome.result.rack_destination
        expected = {
            "kind": "RACK_POSITION" if type(destination) is TransportRackPosition else "ZONE",
            "location_code": destination.location_code,
        }
        if (
            task.request_json.get("rack_id") != binding.resource_fence_id
            or task.request_json.get("rcs_template_id") != "F01"
            or task.request_json.get("target") != expected
        ):
            raise ValueError("return departure frozen destination differs from READY")

    async def member_done(self, db, member):
        return bool(
            await db.scalar(select(member_done_clause(DirectPickExecution)).where(DirectPickExecution.id == member.id))
        )

    async def members(self, db, task_id, rack_id=None):
        statement = select(DirectPickExecution).where(DirectPickExecution.picking_task_id == task_id)
        if rack_id is not None:
            statement = statement.where(DirectPickExecution.rack_id == rack_id)
        return (
            await db.scalars(
                statement.order_by(DirectPickExecution.plan_revision, DirectPickExecution.id).execution_options(
                    populate_existing=True
                )
            )
        ).all()

    async def open_pickups(self, db, task_id, rack_id=None, rack_face=None):
        statement = (
            select(AutomaticPickingPickup.id)
            .join(AutomaticPickingDirectPick, AutomaticPickingDirectPick.id == AutomaticPickingPickup.direct_pick_id)
            .join(DirectPickExecution, DirectPickExecution.id == AutomaticPickingDirectPick.member_id)
            .outerjoin(MaterialExecution, MaterialExecution.id == AutomaticPickingPickup.material_execution_id)
            .where(
                AutomaticPickingDirectPick.picking_task_id == task_id,
                or_(MaterialExecution.id.is_(None), MaterialExecution.status != "CLOSED"),
            )
        )
        if rack_id is not None:
            statement = statement.where(DirectPickExecution.rack_id == rack_id)
        if rack_face is not None:
            statement = statement.where(DirectPickExecution.rack_face == rack_face)
        return await db.scalar(statement.limit(1)) is not None

    async def transport(self, db, transport_id):
        task = await db.scalar(
            select(TransportTask)
            .where(TransportTask.transport_task_id == transport_id)
            .execution_options(populate_existing=True)
        )
        binding = (
            await db.scalar(
                select(TransportDecisionBinding).where(
                    TransportDecisionBinding.client_request_id == task.client_request_id
                )
            )
            if task
            else None
        )
        if task is None or binding is None:
            raise ValueError("return rack lost original Transport/Binding")
        return task, binding

    async def result(self, db, current, transport_id):
        task, binding = await self.transport(db, transport_id)
        if binding.workline_id != current.workline_id or binding.resource_fence_id != current.rack_id:
            raise ValueError("return rack Transport owner differs")
        if task.outcome_version < 1 or task.published_outcome_version != task.outcome_version:
            return None
        fact = await db.scalar(
            select(InboundEvidence).where(
                InboundEvidence.source_identity == f"transport:{transport_id}:outcome:{task.outcome_version}"
            )
        )
        if fact is None or fact.published_at is None:
            return None
        payload = fact.normalized_payload
        if (
            fact.apply_status != "APPLIED"
            or fact.workline_id != current.workline_id
            or fact.transport_task_id != transport_id
            or payload.get("client_request_id") != binding.client_request_id
            or payload.get("step") != binding.step
            or payload.get("source_evidence_id") != binding.source_evidence_id
            or payload.get("status") != task.status
            or payload.get("outcome_version") != task.outcome_version
            or canonical_json_digest(payload) != fact.payload_digest
        ):
            raise ValueError("return rack result differs from original action")
        return fact if task.status == "SUCCEEDED" else None

    async def current_arrival(self, db, current):
        task, binding = await self.transport(db, current.arrival_transport_task_id)
        if binding.picking_task_id != current.picking_task_id or binding.step not in (
            RETURN_RACK_IN,
            RETURN_RACK_ROTATE,
        ):
            raise ValueError("return rack arrival lost exact task/step")
        fact = await self.result(db, current, task.transport_task_id)
        if fact is None:
            return None
        _root, window = await self.transport(db, current.root_transport_task_id)
        if (
            window.step != RETURN_RACK_IN
            or window.picking_task_id != current.picking_task_id
            or window.workline_id != current.workline_id
            or window.resource_fence_id != current.rack_id
        ):
            raise ValueError("return rack original arrival round differs")
        position = await db.scalar(
            select(PositionProjection)
            .where(PositionProjection.object_type == "RACK", PositionProjection.object_id == current.rack_id)
            .execution_options(populate_existing=True)
        )
        members = fact.normalized_payload.get("members", [])
        expected = task.request_json.get("target", task.request_json.get("position"))
        if (
            window.window_released_at is not None
            or position is None
            or position.position_unknown
            or position.source_transport_task_id != task.transport_task_id
            or position.source_causal_token != binding.causal_token
            or position.arrival_face != task.request_json.get("target_face")
            or position.workline_id != current.workline_id
            or position.position_json != expected
            or position.source_effect_phase != "FINAL_RESULT"
            or len(members) != 1
            or members[0].get("object_id") != current.rack_id
            or members[0].get("position_unknown")
            or members[0].get("final_position") != expected
            or members[0].get("arrival_face") != position.arrival_face
            or expected != {"kind": "RACK_POSITION", "location_code": window.window_target_location_code}
        ):
            return None
        # 新的原动作在途时位置即使尚未改变，也不是可继续服务的依据。
        if (
            await db.scalar(
                select(TransportDecisionBinding.id)
                .join(TransportTask, TransportTask.client_request_id == TransportDecisionBinding.client_request_id)
                .where(
                    TransportDecisionBinding.resource_fence_id == current.rack_id,
                    TransportDecisionBinding.causal_token > binding.causal_token,
                    or_(
                        TransportTask.status.not_in(("SUCCEEDED", "REJECTED")),
                        TransportTask.published_outcome_version != TransportTask.outcome_version,
                    ),
                )
                .limit(1)
            )
            is not None
        ):
            return None
        return task, fact, position

    async def discover(self, db, line_id):
        roots = (
            await db.scalars(
                select(TransportTask)
                .join(
                    TransportDecisionBinding,
                    TransportDecisionBinding.client_request_id == TransportTask.client_request_id,
                )
                .where(
                    TransportDecisionBinding.workline_id == line_id,
                    TransportDecisionBinding.step == RETURN_RACK_IN,
                    TransportDecisionBinding.window_target_location_code.is_not(None),
                    TransportDecisionBinding.window_released_at.is_(None),
                    TransportTask.status == "SUCCEEDED",
                )
            )
        ).all()
        for root in roots:
            if (
                await db.scalar(
                    select(AutomaticPickingReturnRack.id).where(
                        AutomaticPickingReturnRack.root_transport_task_id == root.transport_task_id
                    )
                )
                is not None
            ):
                continue
            binding = await db.scalar(
                select(TransportDecisionBinding).where(
                    TransportDecisionBinding.client_request_id == root.client_request_id
                )
            )
            current = AutomaticPickingReturnRack(
                workline_id=line_id,
                picking_task_id=binding.picking_task_id,
                rack_id=binding.resource_fence_id,
                root_transport_task_id=root.transport_task_id,
                arrival_transport_task_id=root.transport_task_id,
            )
            if await self.current_arrival(db, current) is not None:
                db.add(current)
        await db.flush()

    async def pending(self, db, line_id):
        return (
            await db.scalars(
                select(AutomaticPickingReturnRack)
                .where(
                    AutomaticPickingReturnRack.workline_id == line_id,
                    AutomaticPickingReturnRack.closed_evidence_id.is_(None),
                )
                .order_by(AutomaticPickingReturnRack.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).all()

    async def by_decision(self, db, line_id, operation_id):
        return await db.scalar(
            select(AutomaticPickingReturnRack)
            .join(WmsConfirmation, WmsConfirmation.id == AutomaticPickingReturnRack.departure_confirmation_id)
            .where(AutomaticPickingReturnRack.workline_id == line_id, WmsConfirmation.operation_id == operation_id)
        )
