"""调用方持有 WorkLine authority 锁；只消费精确原离场决定。"""

from sqlalchemy import and_, or_, select
from wes_plugin_sdk import RackDepartureReady, TransportRackPosition

from src.app.execution.models import InboundEvidence, PositionProjection, TransportDecisionBinding, WmsConfirmation
from src.app.transport.models import TransportMember, TransportTask
from src.app.wms_integration.outbound_picking.models import PickingTaskBinSourceRack
from src.app.wms_integration.outbound_picking.services.rack_departure import RackDepartureResultReader
from src.utils.canonical_json import canonical_json_digest

from .feed_model import AutomaticPickingFeed
from .return_motion_model import AutomaticPickingReturnMotion
from .source_rack_motion_repository import SourceRackMotionRepository

DEPARTURE_STEP = "AUTOMATIC_PICKING_SOURCE_RACK_OUT"


class SourceRackDepartureRepository:
    def __init__(self, motions, reader=None):
        self.motions = motions
        self.reader = reader or RackDepartureResultReader()

    async def by_id(self, db, identity):
        return await db.get(AutomaticPickingReturnMotion, identity, with_for_update=True)

    async def pending(self, db, line_id):
        return list(
            (
                await db.scalars(
                    select(AutomaticPickingReturnMotion)
                    .where(
                        AutomaticPickingReturnMotion.workline_id == line_id,
                        AutomaticPickingReturnMotion.departure_operation_id.is_not(None),
                        AutomaticPickingReturnMotion.archived_at.is_(None),
                    )
                    .order_by(AutomaticPickingReturnMotion.id)
                    .with_for_update()
                )
            ).all()
        )

    async def by_decision(self, db, line_id, operation_id):
        return await db.scalar(
            select(AutomaticPickingReturnMotion)
            .where(
                AutomaticPickingReturnMotion.workline_id == line_id,
                AutomaticPickingReturnMotion.departure_operation_id == operation_id,
            )
            .with_for_update()
        )

    async def decision(self, db, current, operation_id, *, evidence_id=None):
        if evidence_id is None:
            snapshot = await self.reader.by_operation_id(
                db, workline_id=current.workline_id, operation_id=operation_id, rack_id=current.rack_id
            )
        else:
            snapshot = await self.reader.for_evidence(
                db,
                workline_id=current.workline_id,
                operation_id=operation_id,
                rack_id=current.rack_id,
                evidence_id=evidence_id,
            )
        if snapshot is not None and snapshot.status == "COMPLETED":
            evidence = await db.get(InboundEvidence, snapshot.evidence_id)
            if (
                evidence.workline_id != current.workline_id
                or evidence.picking_task_id is not None
                or evidence.operation_id != operation_id
                or canonical_json_digest(evidence.normalized_payload) != evidence.payload_digest
            ):
                raise ValueError("departure response differs from frozen owner or identity")
        return snapshot

    async def pending_decision(self, db, current):
        request = await db.scalar(
            select(WmsConfirmation).where(
                WmsConfirmation.operation == "outbound.rack.departure_decide@v1",
                WmsConfirmation.operation_id == current.departure_operation_id,
            )
        )
        if request is None:
            raise ValueError("current departure lost its original request")
        if request.status != "COMPLETED":
            return await self.decision(db, current, current.departure_operation_id)
        snapshot = await self.decision(
            db, current, current.departure_operation_id, evidence_id=request.response_evidence_id
        )
        evidence = await db.get(InboundEvidence, snapshot.evidence_id)
        # 正常应用/发布间隙只等待本项；owner、身份和摘要漂移仍由精确 reader 拒绝。
        return snapshot if evidence.published_at is not None else None

    async def current_context(self, db, line, current):
        face, arrival_id = current.rack_face, current.arrival_evidence_id
        if current.rotation_transport_task_id is not None:
            rotation, _ = await SourceRackMotionRepository(self.motions).transport(db, current)
            face, arrival_id = rotation.request_json["target_face"], current.rotation_result_evidence_id
        end = await db.get(InboundEvidence, current.service_end_evidence_id)
        if end is None or end.apply_status != "APPLIED" or end.published_at is None:
            return None
        if canonical_json_digest(end.normalized_payload) != end.payload_digest:
            raise ValueError("departure source service end drift")
        if not await self.motions.current_source(db, line, current, arrival_evidence_id=arrival_id, rack_face=face):
            return None
        return TransportRackPosition(line.position_bindings["FIVE_RACK"]["location_id"]), face

    async def ready(self, db, line, current, operation_id):
        snapshot = await self.decision(db, current, operation_id)
        if (
            snapshot is None
            or snapshot.status != "COMPLETED"
            or type(snapshot.outcome.result) is not RackDepartureReady
        ):
            return None
        context = await self.current_context(db, line, current)
        if context is None:
            return None
        location, face = context
        if snapshot.intent.current_location != location:
            raise ValueError("departure current location differs from source work position")
        if snapshot.intent.current_face != face:
            raise ValueError("departure current face differs from exact arrival")
        if snapshot.intent.task_id is not None or snapshot.completed_at is None:
            raise ValueError("departure lost WorkLine READY identity")
        if snapshot.intent.rack_id != current.rack_id or snapshot.intent.current_face is None:
            raise ValueError("departure requires exact normal source identity")
        arrival_id = current.rotation_result_evidence_id or current.arrival_evidence_id
        arrival = await db.get(InboundEvidence, arrival_id)
        end = await db.get(InboundEvidence, current.service_end_evidence_id)
        request = await db.scalar(
            select(WmsConfirmation).where(
                WmsConfirmation.operation_id == operation_id,
                WmsConfirmation.operation == "outbound.rack.departure_decide@v1",
            )
        )
        if request.created_at < max(current.round_started_at, arrival.received_at, end.received_at):
            raise ValueError("departure READY predates source service")
        return snapshot

    async def superseded(self, db, current):
        fact = await db.get(InboundEvidence, current.rotation_result_evidence_id or current.arrival_evidence_id)
        position = await db.scalar(
            select(PositionProjection).where(
                PositionProjection.object_type == "RACK",
                PositionProjection.object_id == current.rack_id,
            )
        )
        if position is None or fact is None or position.source_transport_task_id == fact.transport_task_id:
            return False
        return await SourceRackMotionRepository(self.motions).superseded(db, current)

    async def service_finished(self, db, current):
        end = await db.get(InboundEvidence, current.service_end_evidence_id)
        if end is None or end.apply_status != "APPLIED" or end.published_at is None:
            return False
        # 来源结束依据显式匹配；只有本轮实际承接的未结束 Feed 能阻止离场。
        pending = await db.scalar(
            select(AutomaticPickingFeed.id)
            .join(PickingTaskBinSourceRack, PickingTaskBinSourceRack.id == AutomaticPickingFeed.source_member_id)
            .where(
                AutomaticPickingFeed.workline_id == current.workline_id,
                AutomaticPickingFeed.picking_task_id == end.picking_task_id,
                PickingTaskBinSourceRack.rack_id == current.rack_id,
                AutomaticPickingFeed.archived_at.is_(None),
                AutomaticPickingFeed.phase.not_in(("FEED_COMPLETE", "CANCELLED")),
                or_(
                    AutomaticPickingFeed.inbound_client_request_id == current.inbound_client_request_id,
                    and_(
                        AutomaticPickingFeed.inbound_client_request_id.is_(None),
                        AutomaticPickingFeed.created_at >= current.round_started_at,
                    ),
                ),
            )
            .limit(1)
        )
        return pending is None

    async def bind(self, db, current, operation_id, transport_id):
        current.departure_operation_id = operation_id
        current.departure_transport_task_id = transport_id
        current.departure_retry_at = None
        await db.flush()

    async def transport(self, db, current):
        task = await db.scalar(
            select(TransportTask).where(TransportTask.transport_task_id == current.departure_transport_task_id)
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
        if binding is None or binding.correlation_id != "departure:" + current.departure_operation_id:
            raise ValueError("departure stage lost original Transport binding")
        await self.validate_transport(db, task, binding)
        return task, binding

    async def validate_transport(self, db, task, binding):
        if (
            task is None
            or task.kind != "RACK_MOVE"
            or task.authority_workline_id != binding.workline_id
            or binding.picking_task_id is not None
            or binding.step != DEPARTURE_STEP
            or not binding.correlation_id.startswith("departure:")
        ):
            raise ValueError("source departure lost WorkLine Transport identity")
        operation_id = binding.correlation_id.removeprefix("departure:")
        snapshot = await self.reader.by_operation_id(
            db, workline_id=binding.workline_id, operation_id=operation_id, rack_id=binding.resource_fence_id
        )
        if (
            snapshot is None
            or snapshot.status != "COMPLETED"
            or type(snapshot.outcome.result) is not RackDepartureReady
            or snapshot.evidence_id != binding.source_evidence_id
        ):
            raise ValueError("departure Transport lost its exact published READY")
        destination = snapshot.outcome.result.rack_destination
        expected = {
            "kind": "RACK_POSITION" if type(destination) is TransportRackPosition else "ZONE",
            "location_code": destination.location_code,
        }
        if (
            task.request_json.get("rack_id") != binding.resource_fence_id
            or task.request_json.get("rcs_template_id") != "CTU03"
            or task.request_json.get("target") != expected
        ):
            raise ValueError("departure frozen Transport differs from READY destination")
        return expected

    async def validate_outcome(self, db, outcome, binding):
        task = await db.scalar(
            select(TransportTask).where(TransportTask.transport_task_id == outcome.transport_task_id)
        )
        if task is None or task.client_request_id != binding.client_request_id:
            raise ValueError("departure outcome lost original Transport")
        await self.validate_transport(db, task, binding)

    async def observe(self, db, line, evidence):
        current = await db.scalar(
            select(AutomaticPickingReturnMotion)
            .where(AutomaticPickingReturnMotion.departure_transport_task_id == evidence.transport_task_id)
            .with_for_update()
        )
        if current is None:
            return "IGNORED"
        payload = evidence.normalized_payload
        if current.archived_at is not None:
            return (
                "SOURCE_DEPARTURE_OLD_OUTCOME"
                if (
                    evidence.workline_id == line.id == current.workline_id
                    and evidence.picking_task_id is None
                    and payload.get("transport_task_id") == current.departure_transport_task_id
                    and payload.get("step") == DEPARTURE_STEP
                    and canonical_json_digest(payload) == evidence.payload_digest
                )
                else "RECONCILING"
            )
        task, binding = await self.transport(db, current)
        if (
            evidence.workline_id != line.id
            or current.workline_id != line.id
            or evidence.picking_task_id is not None
            or payload.get("transport_task_id") != task.transport_task_id
            or payload.get("client_request_id") != task.client_request_id
            or payload.get("source_evidence_id") != binding.source_evidence_id
            or payload.get("step") != DEPARTURE_STEP
            or canonical_json_digest(payload) != evidence.payload_digest
        ):
            return "RECONCILING"
        version = payload.get("outcome_version")
        if type(version) is not int or not 0 < version <= task.outcome_version:
            return "RECONCILING"
        if version < task.outcome_version or current.archived_at is not None:
            return "SOURCE_DEPARTURE_OLD_OUTCOME"
        current.departure_result_evidence_id = evidence.id
        await db.flush()
        return "SOURCE_DEPARTURE_OBSERVED"

    async def rejected(self, db, current):
        task, binding = await self.transport(db, current)
        if task.status != "REJECTED":
            return False
        fact = (
            await db.get(InboundEvidence, current.departure_result_evidence_id)
            if current.departure_result_evidence_id
            else None
        )
        if (
            fact is None
            or fact.apply_status != "APPLIED"
            or fact.published_at is None
            or task.outcome_version < 1
            or task.outcome_version != task.published_outcome_version
        ):
            return False
        payload = fact.normalized_payload
        if (
            fact.kind != "TRANSPORT_RESULT"
            or fact.workline_id != current.workline_id
            or fact.picking_task_id is not None
            or fact.transport_task_id != task.transport_task_id
            or binding.workline_id != current.workline_id
            or binding.resource_fence_id != current.rack_id
            or payload.get("transport_task_id") != task.transport_task_id
            or payload.get("client_request_id") != task.client_request_id
            or payload.get("source_evidence_id") != binding.source_evidence_id
            or payload.get("step") != DEPARTURE_STEP
            or payload.get("status") != "REJECTED"
            or type(payload.get("outcome_version")) is not int
            or payload["outcome_version"] != task.outcome_version
            or canonical_json_digest(payload) != fact.payload_digest
        ):
            raise ValueError("departure rejection differs from original Transport result")
        return True

    async def finished(self, db, current):
        task, binding = await self.transport(db, current)
        fact = (
            await db.get(InboundEvidence, current.departure_result_evidence_id)
            if current.departure_result_evidence_id
            else None
        )
        if (
            fact is None
            or fact.published_at is None
            or task.outcome_version < 1
            or task.outcome_version != task.published_outcome_version
            or fact.normalized_payload.get("outcome_version") != task.outcome_version
        ):
            return False
        if task.status != "SUCCEEDED":
            return await SourceRackMotionRepository(self.motions).superseded(db, current, binding)
        target = await self.validate_transport(db, task, binding)
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
            and members[0].object_id == current.rack_id
            and members[0].status == "SUCCEEDED"
            and not members[0].position_unknown
            and members[0].final_position_json == target
        )
