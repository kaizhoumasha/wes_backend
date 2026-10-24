"""最终转运架的任务归属、当前到位及原 F01 精确读取。"""

from sqlalchemy import or_, select
from wes_plugin_sdk import RackDepartureReady, TransportRackPosition

from src.app.execution.models import InboundEvidence, MaterialExecution, PositionProjection, TransportDecisionBinding
from src.app.execution.repositories import transport_decision_binding_repository as bindings
from src.app.transport.models import TransportTask
from src.app.wms_integration.outbound_picking.services.rack_departure import RackDepartureResultReader
from src.utils.canonical_json import canonical_json_digest

from .final_target_model import AutomaticPickingFinalTargetRack
from .pickup_model import AutomaticPickingDirectPick, AutomaticPickingPickup
from .pickup_repository import PickupRepository
from .return_rack_repository import ReturnRackRepository
from .target_replacement_repository import TARGET_INBOUND_STEP
from .target_rotation_repository import TARGET_ROTATION_STEP
from .work_model import AutomaticPickingWork

FINAL_TARGET_OUT = "AUTOMATIC_PICKING_FINAL_TARGET_OUT"
TARGET_IN = "PICKING_TASK_TARGET_RACK_IN"


class FinalTargetRepository:
    def __init__(self):
        self.transports = ReturnRackRepository()
        self.reader = RackDepartureResultReader()

    async def current(self, db, task_id):
        return await db.scalar(
            select(AutomaticPickingFinalTargetRack)
            .where(AutomaticPickingFinalTargetRack.picking_task_id == task_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def bind(self, db, *, line_id, task_id, transport, binding):
        if (
            binding.workline_id != line_id
            or binding.step not in {TARGET_IN, TARGET_INBOUND_STEP}
            or transport.request_json.get("rack_id") != binding.resource_fence_id
            or transport.client_request_id != binding.client_request_id
            or (binding.step == TARGET_IN and binding.picking_task_id != task_id)
        ):
            raise ValueError("final target lost task/arrival round identity")
        current = await self.current(db, task_id)
        if current is None:
            current = AutomaticPickingFinalTargetRack(
                workline_id=line_id,
                picking_task_id=task_id,
                rack_id=binding.resource_fence_id,
                root_transport_task_id=transport.transport_task_id,
                arrival_transport_task_id=transport.transport_task_id,
            )
            db.add(current)
        elif current.root_transport_task_id != transport.transport_task_id:
            if current.workline_id != line_id or current.departure_confirmation_id is not None:
                raise ValueError("final target departure already froze its original round")
            _, previous = await self.transports.transport(db, current.arrival_transport_task_id)
            if binding.causal_token <= previous.causal_token:
                raise ValueError("final target cannot return to an older arrival round")
            current.rack_id = binding.resource_fence_id
            current.root_transport_task_id = current.arrival_transport_task_id = transport.transport_task_id
        await db.flush()

    async def discover(self, db, line_id):
        roots = (
            await db.execute(
                select(TransportTask, TransportDecisionBinding)
                .join(
                    TransportDecisionBinding,
                    TransportDecisionBinding.client_request_id == TransportTask.client_request_id,
                )
                .where(
                    TransportDecisionBinding.workline_id == line_id,
                    TransportDecisionBinding.step == TARGET_IN,
                    TransportDecisionBinding.window_target_location_code.is_not(None),
                    TransportDecisionBinding.window_released_at.is_(None),
                )
            )
        ).all()
        for transport, binding in roots:
            if await self.current(db, binding.picking_task_id) is None:
                await self.bind(
                    db, line_id=line_id, task_id=binding.picking_task_id, transport=transport, binding=binding
                )

    async def pending(self, db, line_id):
        return (
            await db.scalars(
                select(AutomaticPickingFinalTargetRack)
                .where(
                    AutomaticPickingFinalTargetRack.workline_id == line_id,
                    AutomaticPickingFinalTargetRack.closed_evidence_id.is_(None),
                )
                .order_by(AutomaticPickingFinalTargetRack.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).all()

    async def arrival(self, db, line, current):
        if (
            await db.scalar(
                PickupRepository.for_line(line.id)
                .with_only_columns(AutomaticPickingPickup.id)
                .outerjoin(MaterialExecution, MaterialExecution.id == AutomaticPickingPickup.material_execution_id)
                .where(
                    or_(
                        AutomaticPickingWork.picking_task_id == current.picking_task_id,
                        AutomaticPickingDirectPick.picking_task_id == current.picking_task_id,
                    ),
                    or_(MaterialExecution.id.is_(None), MaterialExecution.status != "CLOSED"),
                )
                .limit(1)
            )
            is not None
        ):
            return None
        await bindings.lock_object_authority(db, object_type="RACK", object_id=current.rack_id)
        root, window = await self.transports.transport(db, current.arrival_transport_task_id)
        if (
            root.transport_task_id != current.root_transport_task_id
            or window.workline_id != line.id
            or window.resource_fence_id != current.rack_id
            or window.step not in {TARGET_IN, TARGET_INBOUND_STEP}
            or (window.step == TARGET_IN and window.picking_task_id != current.picking_task_id)
            or root.kind != "RACK_MOVE"
            or root.authority_workline_id != line.id
            or root.request_json.get("rcs_template_id") != "F01"
        ):
            raise ValueError("final target original arrival differs")
        position = await db.scalar(
            select(PositionProjection)
            .where(
                PositionProjection.object_type == "RACK",
                PositionProjection.object_id == current.rack_id,
            )
            .execution_options(populate_existing=True)
        )
        expected = {
            "kind": "RACK_POSITION",
            "location_code": line.position_bindings.get("TRANSFER_RACK", {}).get("location_id"),
        }
        if (
            position is None
            or position.position_unknown
            or position.workline_id != line.id
            or position.position_json != expected
            or position.source_effect_phase != "FINAL_RESULT"
            or window.window_target_location_code != expected["location_code"]
            or window.window_released_at is not None
            or position.arrival_face is None
        ):
            return None
        root_fact = await self.arrival_fact(db, current, root, window)
        if root_fact is None or root.request_json.get("target") != expected:
            return None
        actual, binding = await self.transports.transport(db, position.source_transport_task_id)
        if actual.transport_task_id != root.transport_task_id and (
            binding.step != TARGET_ROTATION_STEP
            or actual.kind != "RACK_ROTATE"
            or actual.request_json.get("position") != expected
            or binding.causal_token <= window.causal_token
        ):
            return None
        fact = await self.arrival_fact(db, current, actual, binding)
        members = fact.normalized_payload.get("members", []) if fact else []
        if (
            fact is None
            or position.source_causal_token != binding.causal_token
            or actual.request_json.get("target_face") != position.arrival_face
            or len(members) != 1
            or members[0].get("object_id") != current.rack_id
            or members[0].get("position_unknown")
            or members[0].get("final_position") != expected
            or members[0].get("arrival_face") != position.arrival_face
        ):
            return None
        # 任何更新的实际搬运义务仍在途/未知时，旧到位不是新离场依据。
        if (
            await db.scalar(
                select(TransportDecisionBinding.id)
                .join(
                    TransportTask,
                    TransportTask.client_request_id == TransportDecisionBinding.client_request_id,
                )
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
        return TransportRackPosition(expected["location_code"]), position.arrival_face, fact

    async def arrival_fact(self, db, current, task, binding):
        if binding.step != TARGET_IN:
            return await self.transports.result(db, current, task.transport_task_id)
        # 初始进场由宿主应用位置；插件未消费的 IGNORED 不抹掉已发布的原 Transport 事实。
        if (
            task.status != "SUCCEEDED"
            or task.outcome_version < 1
            or task.published_outcome_version != task.outcome_version
        ):
            return None
        fact = await db.scalar(
            select(InboundEvidence).where(
                InboundEvidence.source_identity == f"transport:{task.transport_task_id}:outcome:{task.outcome_version}",
            )
        )
        if fact is None or (fact.published_at is None and fact.apply_status != "IGNORED"):
            return None
        payload = fact.normalized_payload
        if (
            fact.kind != "TRANSPORT_RESULT"
            or fact.apply_status not in ("APPLIED", "IGNORED")
            or fact.workline_id != current.workline_id
            or fact.picking_task_id != current.picking_task_id
            or fact.transport_task_id != task.transport_task_id
            or payload.get("transport_task_id") != task.transport_task_id
            or payload.get("client_request_id") != binding.client_request_id
            or payload.get("step") != TARGET_IN
            or payload.get("source_evidence_id") != binding.source_evidence_id
            or payload.get("status") != "SUCCEEDED"
            or payload.get("outcome_version") != task.outcome_version
            or canonical_json_digest(payload) != fact.payload_digest
        ):
            raise ValueError("final target initial arrival differs from original result")
        return fact

    async def decision(self, db, current):
        return await self.reader.by_id(
            db, picking_task_id=current.picking_task_id, confirmation_id=current.departure_confirmation_id
        )

    async def validate_transport(self, db, transport_id, client_request_id, binding):
        current = await db.scalar(
            select(AutomaticPickingFinalTargetRack).where(
                AutomaticPickingFinalTargetRack.departure_transport_task_id == transport_id,
            )
        )
        if current is None:
            raise ValueError("final target result lost its original current")
        task, original = await self.transports.transport(db, transport_id)
        snapshot = await self.decision(db, current)
        destination = (
            snapshot.outcome.result.rack_destination
            if snapshot and snapshot.outcome and type(snapshot.outcome.result) is RackDepartureReady
            else None
        )
        expected = (
            {
                "kind": "RACK_POSITION" if type(destination) is TransportRackPosition else "ZONE",
                "location_code": destination.location_code,
            }
            if destination
            else None
        )
        if (
            original.id != binding.id
            or binding.step != FINAL_TARGET_OUT
            or binding.workline_id != current.workline_id
            or binding.picking_task_id != current.picking_task_id
            or task.client_request_id != client_request_id
            or task.kind != "RACK_MOVE"
            or task.authority_workline_id != current.workline_id
            or binding.resource_fence_id != current.rack_id
            or snapshot is None
            or snapshot.status != "COMPLETED"
            or snapshot.outcome is None
            or type(snapshot.outcome.result) is not RackDepartureReady
            or binding.correlation_id != snapshot.intent.operation_id
            or binding.source_evidence_id != snapshot.evidence_id
            or task.request_json.get("rcs_template_id") != "F01"
            or task.request_json.get("rack_id") != current.rack_id
            or task.request_json.get("target") != expected
        ):
            raise ValueError("final target result differs from original READY/F01")
