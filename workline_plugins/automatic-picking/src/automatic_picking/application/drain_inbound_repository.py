"""排空进场的精确绑定与当前到位引用；调用方持有 WorkLine 锁。"""

from sqlalchemy import select
from wes_plugin_sdk import ReturnBufferDrainReady

from src.app.execution.models import InboundEvidence, TransportDecisionBinding
from src.app.transport.models import TransportTask
from src.app.wms_integration.return_buffer_drain import ReturnBufferDrainResultReader
from src.utils.canonical_json import canonical_json_digest

from .drain_model import AutomaticPickingDrain, AutomaticPickingDrainRack
from .return_motion_repository import ReturnMotionRepository

DRAIN_INBOUND_STEP = "AUTOMATIC_PICKING_DRAIN_RACK_IN"


class DrainInboundRepository:
    async def racks(self, db, drain_id):
        return list(
            (
                await db.scalars(
                    select(AutomaticPickingDrainRack)
                    .where(AutomaticPickingDrainRack.drain_id == drain_id)
                    .order_by(AutomaticPickingDrainRack.rack_id)
                )
            ).all()
        )

    async def binding(self, db, line_id, operation_id, rack_id):
        return await db.scalar(
            select(TransportDecisionBinding).where(
                TransportDecisionBinding.workline_id == line_id,
                TransportDecisionBinding.correlation_id == f"drain:{operation_id}:{rack_id}",
                TransportDecisionBinding.step == DRAIN_INBOUND_STEP,
            )
        )

    async def validate_transport(self, db, task, binding):
        row = await db.scalar(
            select(AutomaticPickingDrainRack).where(AutomaticPickingDrainRack.binding_id == binding.id)
        )
        root = await db.get(AutomaticPickingDrain, row.drain_id) if row else None
        if (
            root is None
            or binding.workline_id != root.workline_id
            or binding.picking_task_id is not None
            or binding.step != DRAIN_INBOUND_STEP
            or binding.resource_fence_id != row.rack_id
            or binding.correlation_id != f"drain:{root.operation_id}:{row.rack_id}"
            or binding.source_evidence_id != root.response_evidence_id
        ):
            raise ValueError("drain inbound lost exact reservation binding")
        snapshot = await ReturnBufferDrainResultReader().for_operation(
            db, workline_id=root.workline_id, operation_id=root.operation_id
        )
        if (
            snapshot is None
            or type(snapshot.result) is not ReturnBufferDrainReady
            or snapshot.evidence_id != root.response_evidence_id
        ):
            raise ValueError("drain inbound requires original published READY")
        rack = next((r for r in snapshot.result.racks if r.rack_id == row.rack_id), None)
        target = {"kind": "RACK_POSITION", "location_code": binding.window_target_location_code}
        if (
            task is None
            or task.client_request_id != binding.client_request_id
            or task.kind != "RACK_MOVE"
            or task.authority_workline_id != root.workline_id
            or rack is None
            or not binding.window_target_location_code
            or task.request_json.get("rack_id") != row.rack_id
            or task.request_json.get("source") != {"kind": "RACK", "location_code": row.rack_id}
            or task.request_json.get("target") != target
            or task.request_json.get("target_face") != rack.rack_faces[0]
            or task.request_json.get("rcs_template_id") != "CTU01"
        ):
            raise ValueError("drain inbound Transport differs from frozen first face/position")
        return root, row, target

    async def validate_outcome(self, db, outcome, binding):
        task = await db.scalar(
            select(TransportTask).where(TransportTask.transport_task_id == outcome.transport_task_id)
        )
        await self.validate_transport(db, task, binding)

    async def observe(self, db, line, evidence):
        payload = evidence.normalized_payload
        task = await db.scalar(
            select(TransportTask).where(TransportTask.transport_task_id == evidence.transport_task_id)
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
        if binding is None:
            return "RECONCILING"
        root, _row, _ = await self.validate_transport(db, task, binding)
        if (
            root.workline_id != line.id
            or evidence.workline_id != line.id
            or evidence.picking_task_id is not None
            or evidence.kind != "TRANSPORT_RESULT"
            or evidence.apply_status != "APPLIED"
            or payload.get("transport_task_id") != task.transport_task_id
            or payload.get("client_request_id") != binding.client_request_id
            or payload.get("source_evidence_id") != binding.source_evidence_id
            or payload.get("step") != DRAIN_INBOUND_STEP
            or canonical_json_digest(payload) != evidence.payload_digest
            or type(payload.get("outcome_version")) is not int
            or not 0 < payload["outcome_version"] <= task.outcome_version
        ):
            return "RECONCILING"
        if root.archived_at is not None or payload["outcome_version"] < task.outcome_version:
            return "IGNORED"
        frozen = {key: value for key, value in payload.items() if key not in {"step", "source_evidence_id"}}
        if canonical_json_digest(frozen) != canonical_json_digest(task.outcome_json):
            return "RECONCILING"
        # 发布后由 batch 重验物理到位并写交接；FactProcessor 事务不提前把自己标为 published。
        return "DRAIN_INBOUND_OBSERVED"

    async def current_arrival(self, db, line, row):
        binding = await db.get(TransportDecisionBinding, row.binding_id)
        task = await db.scalar(
            select(TransportTask).where(TransportTask.client_request_id == binding.client_request_id)
        )
        root, _, target = await self.validate_transport(db, task, binding)
        if root.archived_at is not None:
            return None
        fact = await db.scalar(
            select(InboundEvidence).where(
                InboundEvidence.source_identity == f"transport:{task.transport_task_id}:outcome:{task.outcome_version}",
                InboundEvidence.kind == "TRANSPORT_RESULT",
            )
        )
        if fact is None or await self.observe(db, line, fact) != "DRAIN_INBOUND_OBSERVED":
            return None
        if not await ReturnMotionRepository().current_arrival(
            db, line.id, fact.id, row.rack_id, task.request_json["target_face"], target
        ):
            return None
        return task, fact

    async def recover_arrival(self, db, line, row):
        arrival = await self.current_arrival(db, line, row)
        if arrival is None:
            return 0
        _task, fact = arrival
        if row.arrival_evidence_id is not None:
            if row.arrival_evidence_id != fact.id:
                raise ValueError("drain arrival identity drift")
            return 0
        row.arrival_evidence_id = fact.id
        await db.flush()
        return 1
