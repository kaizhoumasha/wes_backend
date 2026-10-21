"""目标 ROTATE 的当前动作与事实读取；调用方沿用 WorkLine→Work→Pickup 锁。"""

from sqlalchemy import select

from src.app.execution.models import InboundEvidence, PositionProjection
from src.app.execution.repositories import transport_decision_binding_repository
from src.app.transport.models import TransportTask
from src.utils.canonical_json import canonical_json_digest

from .pickup_model import AutomaticPickingPickup
from .pickup_repository import PickupRepository

TARGET_ROTATION_STEP = "AUTOMATIC_PICKING_TARGET_ROTATE"


class TargetRotationRepository(PickupRepository):
    async def pickup_by_material(self, db, material_id):
        return await db.scalar(
            select(AutomaticPickingPickup.pickup_id).where(AutomaticPickingPickup.material_execution_id == material_id)
        )

    async def pickup_by_transport(self, db, transport_id):
        return await db.scalar(
            select(AutomaticPickingPickup.pickup_id).where(
                AutomaticPickingPickup.target_rotation_transport_task_id == transport_id
            )
        )

    async def position(self, db, rack_id):
        await transport_decision_binding_repository.lock_object_authority(db, object_type="RACK", object_id=rack_id)
        return await db.scalar(
            select(PositionProjection)
            .where(PositionProjection.object_type == "RACK", PositionProjection.object_id == rack_id)
            .execution_options(populate_existing=True)
        )

    async def transport(self, db, work, pickup, target):
        task = await db.scalar(
            select(TransportTask)
            .where(TransportTask.transport_task_id == pickup.target_rotation_transport_task_id)
            .execution_options(populate_existing=True)
        )
        binding = (
            await transport_decision_binding_repository.get_by_client_request_id(db, task.client_request_id)
            if task
            else None
        )
        if (
            task is None
            or binding is None
            or task.kind != "RACK_ROTATE"
            or task.authority_workline_id != work.workline_id
            or task.caller_json != {"workline_id": str(work.workline_id), "station_id": None}
            or binding.workline_id != work.workline_id
            or binding.picking_task_id != work.picking_task_id
            or binding.source_evidence_id != pickup.accept_evidence_id
            or binding.correlation_id != "auto-target-rotation:" + pickup.pickup_id
            or binding.step != TARGET_ROTATION_STEP
            or binding.resource_fence_id != target.rack_id
            or task.request_json.get("rack_id") != target.rack_id
            or task.request_json.get("target_face") != target.rack_face
            or task.request_json.get("rcs_template_id") != "CTU02"
        ):
            raise ValueError("target rotation lost original Transport identity")
        return task, binding

    def matches(self, evidence, task, binding):
        payload = evidence.normalized_payload
        return (
            evidence.kind == "TRANSPORT_RESULT"
            and evidence.apply_status == "APPLIED"
            and evidence.transport_task_id == task.transport_task_id
            and evidence.workline_id == binding.workline_id
            and evidence.picking_task_id == binding.picking_task_id
            and payload.get("transport_task_id") == task.transport_task_id
            and payload.get("client_request_id") == task.client_request_id
            and payload.get("step") == TARGET_ROTATION_STEP
            and payload.get("source_evidence_id") == binding.source_evidence_id
            and canonical_json_digest(payload) == evidence.payload_digest
        )

    async def ready(self, db, work, pickup, target):
        task, binding = await self.transport(db, work, pickup, target)
        if (
            task.status != "SUCCEEDED"
            or task.outcome_version < 1
            or task.outcome_version != task.published_outcome_version
        ):
            return False
        evidence = await db.scalar(
            select(InboundEvidence)
            .where(
                InboundEvidence.source_identity == f"transport:{task.transport_task_id}:outcome:{task.outcome_version}"
            )
            .execution_options(populate_existing=True)
        )
        if evidence is None or evidence.published_at is None or not self.matches(evidence, task, binding):
            return False
        payload = evidence.normalized_payload
        members = payload.get("members", [])
        expected = task.request_json.get("position")
        position = await self.position(db, target.rack_id)
        return (
            payload.get("outcome_version") == task.outcome_version
            and payload.get("status") == "SUCCEEDED"
            and len(members) == 1
            and members[0].get("object_id") == target.rack_id
            and members[0].get("final_position") == expected
            and not members[0].get("position_unknown")
            and members[0].get("arrival_face") == target.rack_face
            and position is not None
            and position.workline_id == work.workline_id
            and not position.position_unknown
            and position.position_json == expected
            and position.arrival_face == target.rack_face
            and position.source_transport_task_id == task.transport_task_id
            and position.source_causal_token == binding.causal_token
            and position.source_effect_phase == "FINAL_RESULT"
        )
