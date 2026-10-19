"""原 drain 离场动作的冻结依据与结果；不依赖当前候选或父流程状态。"""

from sqlalchemy import select
from wes_plugin_sdk import RackDepartureReady, TransportRackPosition

from src.app.execution.models import InboundEvidence, TransportDecisionBinding, WmsConfirmation
from src.app.transport.models import TransportMember, TransportTask
from src.app.wms_integration.outbound_picking.services.rack_departure import RackDepartureResultReader
from src.utils.canonical_json import canonical_json_digest

from .drain_model import AutomaticPickingDrain
from .return_motion_model import AutomaticPickingReturnMotion

DRAIN_DEPARTURE_STEP = "AUTOMATIC_PICKING_DRAIN_RACK_OUT"


class DrainDepartureRepository:
    def __init__(self):
        self.reader = RackDepartureResultReader()

    async def pending(self, db, line_id):
        return list(
            (
                await db.scalars(
                    select(AutomaticPickingReturnMotion)
                    .join(
                        AutomaticPickingDrain,
                        AutomaticPickingDrain.response_evidence_id == AutomaticPickingReturnMotion.source_evidence_id,
                    )
                    .where(
                        AutomaticPickingReturnMotion.workline_id == line_id,
                        AutomaticPickingReturnMotion.departure_operation_id.is_not(None),
                        AutomaticPickingReturnMotion.inbound_client_request_id.is_(None),
                        AutomaticPickingDrain.workline_id == line_id,
                        AutomaticPickingReturnMotion.archived_at.is_(None),
                    )
                    .with_for_update(of=AutomaticPickingReturnMotion)
                )
            ).all()
        )

    async def by_decision(self, db, line_id, operation_id):
        return await db.scalar(
            select(AutomaticPickingReturnMotion)
            .join(
                AutomaticPickingDrain,
                AutomaticPickingDrain.response_evidence_id == AutomaticPickingReturnMotion.source_evidence_id,
            )
            .where(
                AutomaticPickingReturnMotion.workline_id == line_id,
                AutomaticPickingDrain.workline_id == line_id,
                AutomaticPickingReturnMotion.inbound_client_request_id.is_(None),
                AutomaticPickingReturnMotion.departure_operation_id == operation_id,
            )
            .with_for_update(of=AutomaticPickingReturnMotion)
        )

    async def decision(self, db, stage, *, evidence=None):
        request = await db.scalar(
            select(WmsConfirmation).where(
                WmsConfirmation.operation == "outbound.rack.departure_decide@v1",
                WmsConfirmation.operation_id == stage.departure_operation_id,
            )
        )
        if request is None:
            raise ValueError("drain departure lost current request")
        if request.status != "COMPLETED":
            if evidence is not None:
                raise ValueError("drain departure result has no completed request")
            return await self.reader.by_operation_id(
                db, workline_id=stage.workline_id, operation_id=stage.departure_operation_id, rack_id=stage.rack_id
            )
        fact = evidence if evidence is not None else await db.get(InboundEvidence, request.response_evidence_id)
        snapshot = await self.reader.for_evidence(
            db,
            workline_id=stage.workline_id,
            operation_id=stage.departure_operation_id,
            rack_id=stage.rack_id,
            evidence_id=fact.id,
        )
        if (
            fact.workline_id != stage.workline_id
            or fact.picking_task_id is not None
            or canonical_json_digest(fact.normalized_payload) != fact.payload_digest
        ):
            raise ValueError("drain departure result differs from frozen owner or digest")
        return snapshot if evidence is not None or fact.published_at is not None else None

    async def validate_transport(self, db, task, binding):
        if (
            task is None
            or task.kind != "RACK_MOVE"
            or binding.step != DRAIN_DEPARTURE_STEP
            or binding.picking_task_id is not None
            or task.authority_workline_id != binding.workline_id
            or task.client_request_id != binding.client_request_id
            or not binding.correlation_id.startswith("departure:")
        ):
            raise ValueError("drain departure lost original WorkLine action")
        snapshot = await self.reader.by_operation_id(
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
            or snapshot.intent.task_id is not None
        ):
            raise ValueError("drain departure lost exact published READY")
        destination = snapshot.outcome.result.rack_destination
        target = {
            "kind": "RACK_POSITION" if type(destination) is TransportRackPosition else "ZONE",
            "location_code": destination.location_code,
        }
        if (
            task.request_json.get("rack_id") != binding.resource_fence_id
            or task.request_json.get("rcs_template_id") != "CTU03"
            or task.request_json.get("target") != target
        ):
            raise ValueError("drain departure differs from frozen destination")
        return target

    async def validate_outcome(self, db, outcome, binding):
        task = await db.scalar(
            select(TransportTask).where(TransportTask.transport_task_id == outcome.transport_task_id)
        )
        if task is None or task.client_request_id != outcome.client_request_id:
            raise ValueError("drain departure outcome lost original action")
        await self.validate_transport(db, task, binding)

    async def transport(self, db, stage):
        task = await db.scalar(
            select(TransportTask).where(TransportTask.transport_task_id == stage.departure_transport_task_id)
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
        if (
            binding is None
            or binding.workline_id != stage.workline_id
            or binding.resource_fence_id != stage.rack_id
            or binding.correlation_id != "departure:" + stage.departure_operation_id
        ):
            raise ValueError("drain stage lost exact departure binding")
        target = await self.validate_transport(db, task, binding)
        return task, binding, target

    async def observe(self, db, line, evidence):
        stage = await db.scalar(
            select(AutomaticPickingReturnMotion)
            .where(AutomaticPickingReturnMotion.departure_transport_task_id == evidence.transport_task_id)
            .with_for_update()
        )
        if stage is None:
            return "IGNORED"
        task, binding, _ = await self.transport(db, stage)
        payload = evidence.normalized_payload
        version = payload.get("outcome_version")
        if (
            evidence.kind != "TRANSPORT_RESULT"
            or evidence.workline_id != line.id
            or stage.workline_id != line.id
            or evidence.picking_task_id is not None
            or evidence.apply_status != "APPLIED"
            or evidence.transport_task_id != task.transport_task_id
            or payload.get("transport_task_id") != task.transport_task_id
            or payload.get("client_request_id") != task.client_request_id
            or payload.get("step") != DRAIN_DEPARTURE_STEP
            or payload.get("source_evidence_id") != binding.source_evidence_id
            or canonical_json_digest(payload) != evidence.payload_digest
            or type(version) is not int
            or not 0 < version <= task.outcome_version
        ):
            return "RECONCILING"
        if version < task.outcome_version or stage.archived_at is not None:
            return "DRAIN_DEPARTURE_OLD_OUTCOME"
        frozen = {k: v for k, v in payload.items() if k not in {"step", "source_evidence_id"}}
        if canonical_json_digest(frozen) != canonical_json_digest(task.outcome_json):
            return "RECONCILING"
        stage.departure_result_evidence_id = evidence.id
        await db.flush()
        return "DRAIN_DEPARTURE_OBSERVED"

    async def rejected(self, db, stage):
        task, _, _ = await self.transport(db, stage)
        if task.status != "REJECTED":
            return False
        fact = (
            await db.get(InboundEvidence, stage.departure_result_evidence_id)
            if stage.departure_result_evidence_id
            else None
        )
        if (
            fact is None
            or fact.published_at is None
            or task.outcome_version < 1
            or task.outcome_version != task.published_outcome_version
        ):
            return False
        if (
            fact.normalized_payload.get("outcome_version") != task.outcome_version
            or fact.normalized_payload.get("status") != "REJECTED"
        ):
            raise ValueError("drain rejection differs from original published result")
        return True

    async def finished(self, db, stage):
        task, _, target = await self.transport(db, stage)
        fact = (
            await db.get(InboundEvidence, stage.departure_result_evidence_id)
            if stage.departure_result_evidence_id
            else None
        )
        if (
            task.status != "SUCCEEDED"
            or fact is None
            or fact.published_at is None
            or task.outcome_version < 1
            or task.outcome_version != task.published_outcome_version
            or fact.normalized_payload.get("outcome_version") != task.outcome_version
        ):
            return False
        members = list(
            (
                await db.scalars(
                    select(TransportMember).where(TransportMember.transport_task_id == task.transport_task_id)
                )
            ).all()
        )
        return (
            len(members) == 1
            and members[0].object_type == "RACK"
            and members[0].object_id == stage.rack_id
            and members[0].status == "SUCCEEDED"
            and not members[0].position_unknown
            and isinstance(members[0].final_position_json, dict)
            and members[0].final_position_json.get("kind") == "RACK_POSITION"
            and bool(members[0].final_position_json.get("location_code"))
            and (target["kind"] == "ZONE" or members[0].final_position_json == target)
        )
