"""REPLACE 原离场义务；创建校验当前轮次，恢复只读原 Binding/READY/Transport。"""

from sqlalchemy import exists, or_, select, update
from wes_plugin_sdk import PickingMaterialAccept, PickingTargetReplace, RackDepartureReady

from src.app.execution.models import (
    InboundEvidence,
    MaterialExecution,
    PositionProjection,
    TransportDecisionBinding,
    WmsConfirmation,
)
from src.app.execution.repositories import transport_decision_binding_repository as bindings
from src.app.runtime.orchestration.models.workline_position import WorkLinePosition
from src.app.transport.models import TransportTask
from src.app.wms_integration.outbound_picking.services import MaterialDecideResultReader
from src.app.wms_integration.outbound_picking.services.rack_departure import RackDepartureResultReader
from src.app.workline.models import WorkLine
from src.utils.canonical_json import canonical_json_digest

from .pickup_model import AutomaticPickingPickup
from .target_rotation_repository import TARGET_ROTATION_STEP, TargetRotationRepository
from .work_model import AutomaticPickingWork

TARGET_DEPARTURE_STEP = "AUTOMATIC_PICKING_TARGET_REPLACE_OUT"
TARGET_INBOUND_STEP = "AUTOMATIC_PICKING_TARGET_REPLACE_IN"
ROUND_PREFIX = "auto-target-out:"


class TargetReplacementRepository:
    def __init__(self, reader=None):
        self.reader = reader or RackDepartureResultReader()

    async def decision(self, db, line_id, operation_id, rack_id):
        snapshot = await self.reader.by_operation_id(
            db, workline_id=line_id, operation_id=operation_id, rack_id=rack_id
        )
        if (
            snapshot is None
            or snapshot.status != "COMPLETED"
            or type(snapshot.outcome.result) is not RackDepartureReady
        ):
            return None
        fact = await db.get(InboundEvidence, snapshot.evidence_id)
        if (
            fact is None
            or fact.workline_id != line_id
            or fact.picking_task_id is not None
            or fact.published_at is None
            or canonical_json_digest(fact.normalized_payload) != fact.payload_digest
        ):
            raise ValueError("target departure READY lost published owner or digest")
        return snapshot

    async def current_arrival(self, db, line_id, observed, snapshot=None):
        await bindings.lock_object_authority(db, object_type="RACK", object_id=observed.old_rack_id)
        arrival = await db.get(InboundEvidence, observed.arrival_evidence_id)
        task = await db.scalar(
            select(TransportTask).where(TransportTask.client_request_id == observed.inbound_client_request_id)
        )
        binding = await bindings.get_by_client_request_id(db, observed.inbound_client_request_id)
        line = await db.get(WorkLine, line_id)
        request = await db.scalar(
            select(WmsConfirmation).where(
                WmsConfirmation.operation == "outbound.rack.departure_decide@v1",
                WmsConfirmation.operation_id == observed.departure_operation_id,
            )
        )
        position = await db.scalar(
            select(PositionProjection)
            .where(PositionProjection.object_type == "RACK", PositionProjection.object_id == observed.old_rack_id)
            .execution_options(populate_existing=True)
        )
        expected = {
            "kind": "RACK_POSITION",
            "location_code": (
                snapshot.intent.current_location.location_code
                if snapshot is not None
                else line.position_bindings.get("TRANSFER_RACK", {}).get("location_id")
            ),
        }
        face = snapshot.intent.current_face if snapshot is not None else (position.arrival_face if position else None)
        current_task, current_binding, current_fact = task, binding, arrival
        if position is not None and task is not None and position.source_transport_task_id != task.transport_task_id:
            current_task = await db.scalar(
                select(TransportTask).where(TransportTask.transport_task_id == position.source_transport_task_id)
            )
            current_binding = (
                await bindings.get_by_client_request_id(db, current_task.client_request_id) if current_task else None
            )
            current_fact = (
                await db.scalar(
                    select(InboundEvidence).where(
                        InboundEvidence.source_identity
                        == f"transport:{current_task.transport_task_id}:outcome:{current_task.outcome_version}"
                    )
                )
                if current_task
                else None
            )
            if (
                binding is None
                or current_task is None
                or current_binding is None
                or current_fact is None
                or current_task.kind != "RACK_ROTATE"
                or current_task.status != "SUCCEEDED"
                or current_task.authority_workline_id != line_id
                or current_task.request_json.get("rack_id") != observed.old_rack_id
                or current_task.request_json.get("position") != expected
                or current_task.request_json.get("target_face") != face
                or current_task.outcome_version < 1
                or current_task.published_outcome_version != current_task.outcome_version
                or current_binding.workline_id != line_id
                or current_binding.step != TARGET_ROTATION_STEP
                or current_binding.resource_fence_id != observed.old_rack_id
                or current_binding.causal_token <= binding.causal_token
                or current_fact.published_at is None
                or current_fact.normalized_payload.get("outcome_version") != current_task.outcome_version
                or not TargetRotationRepository(None).matches(current_fact, current_task, current_binding)
            ):
                return False
        return not (
            task is None
            or binding is None
            or arrival is None
            or position is None
            or task.kind != "RACK_MOVE"
            or task.status != "SUCCEEDED"
            or task.authority_workline_id != line_id
            or binding.workline_id != line_id
            or binding.step not in {"PICKING_TASK_TARGET_RACK_IN", TARGET_INBOUND_STEP}
            or binding.resource_fence_id != observed.old_rack_id
            or task.request_json.get("rcs_template_id") != "F01"
            or task.request_json.get("rack_id") != observed.old_rack_id
            or task.request_json.get("target") != expected
            or task.outcome_version < 1
            or task.published_outcome_version != task.outcome_version
            or arrival.kind != "TRANSPORT_RESULT"
            or arrival.apply_status not in ("APPLIED", "IGNORED")
            or arrival.transport_task_id != task.transport_task_id
            or arrival.workline_id != line_id
            or arrival.picking_task_id != binding.picking_task_id
            or (arrival.published_at is None and arrival.apply_status != "IGNORED")
            or arrival.normalized_payload.get("status") != "SUCCEEDED"
            or arrival.normalized_payload.get("client_request_id") != task.client_request_id
            or arrival.normalized_payload.get("transport_task_id") != task.transport_task_id
            or arrival.normalized_payload.get("step") != binding.step
            or arrival.normalized_payload.get("source_evidence_id") != binding.source_evidence_id
            or arrival.normalized_payload.get("outcome_version") != task.outcome_version
            or canonical_json_digest(arrival.normalized_payload) != arrival.payload_digest
            or position.workline_id != line_id
            or position.position_unknown
            or position.position_json != expected
            or position.arrival_face != face
            or binding.window_target_location_code != expected["location_code"]
            or binding.window_released_at is not None
            or position.source_transport_task_id != current_task.transport_task_id
            or position.source_causal_token != current_binding.causal_token
            or position.source_effect_phase != "FINAL_RESULT"
            or line.position_bindings.get("TRANSFER_RACK", {}).get("location_id") != expected["location_code"]
            or face is None
            or (
                snapshot is not None
                and (
                    snapshot.completed_at < arrival.received_at
                    or request is None
                    or request.created_at < current_fact.received_at
                    or snapshot.outcome.result.rack_destination == snapshot.intent.current_location
                )
            )
        )

    async def current_context(self, db, line_id, observed):
        if not await self.current_arrival(db, line_id, observed):
            return None
        position = await db.scalar(
            select(PositionProjection).where(
                PositionProjection.object_type == "RACK", PositionProjection.object_id == observed.old_rack_id
            )
        )
        from wes_plugin_sdk import TransportRackPosition

        return TransportRackPosition(position.position_json["location_code"]), position.arrival_face

    async def operation_request(self, db, line_id, operation_id):
        row = await db.scalar(
            select(WmsConfirmation).where(
                WmsConfirmation.operation == "outbound.rack.departure_decide@v1",
                WmsConfirmation.operation_id == operation_id,
            )
        )
        if row is None:
            raise ValueError("target departure request was not persisted")
        return await self.request(db, line_id, row.id)

    async def request(self, db, line_id, confirmation_id):
        request = await db.get(WmsConfirmation, confirmation_id)
        if (
            request is None
            or request.operation != "outbound.rack.departure_decide@v1"
            or request.workline_id != line_id
            or request.picking_task_id is not None
            or canonical_json_digest(request.request_payload) != request.request_digest
        ):
            raise ValueError("target departure lost current request owner or identity")
        return request

    async def by_decision(self, db, line_id, operation_id):
        return await db.scalar(
            select(AutomaticPickingPickup)
            .join(AutomaticPickingWork, AutomaticPickingWork.id == AutomaticPickingPickup.work_id)
            .join(WmsConfirmation, WmsConfirmation.id == AutomaticPickingPickup.target_departure_confirmation_id)
            .where(
                AutomaticPickingWork.workline_id == line_id,
                WmsConfirmation.operation_id == operation_id,
                WmsConfirmation.operation == "outbound.rack.departure_decide@v1",
            )
            .order_by(AutomaticPickingPickup.id)
        )

    @staticmethod
    def inbound_correlation(departure_id, rack_id):
        return "auto-target-in:" + canonical_json_digest({"departure": departure_id, "rack": rack_id})

    async def shared_target_round(self, db, line_id, pickup, target):
        peers = (
            await db.scalars(
                select(AutomaticPickingPickup)
                .join(AutomaticPickingWork, AutomaticPickingWork.id == AutomaticPickingPickup.work_id)
                .join(MaterialExecution, MaterialExecution.id == AutomaticPickingPickup.material_execution_id)
                .where(
                    AutomaticPickingWork.workline_id == line_id,
                    AutomaticPickingPickup.pickup_id != pickup.pickup_id,
                    AutomaticPickingPickup.target_departure_transport_task_id.is_not(None),
                    MaterialExecution.status != "CLOSED",
                )
            )
        ).all()
        rounds = set()
        reader = MaterialDecideResultReader()
        for peer in peers:
            accepted = (
                await reader.by_id(
                    db, material_execution_id=peer.material_execution_id, confirmation_id=peer.material_confirmation_id
                )
                if peer.material_confirmation_id is not None
                else await reader.by_operation_id(
                    db, material_execution_id=peer.material_execution_id, operation_id=peer.pickup_id
                )
            )
            if (
                accepted is None
                or accepted.status != "COMPLETED"
                or accepted.published_at is None
                or accepted.evidence_id != peer.accept_evidence_id
                or type(accepted.outcome.result) is not PickingMaterialAccept
                or type(accepted.outcome.result.target_preparation) is not PickingTargetReplace
            ):
                continue
            destination = accepted.outcome.result.target_locator
            if (destination.rack_id, destination.rack_face) != (target.rack_id, target.rack_face):
                continue
            original, binding = await self.transport(db, line_id, peer.target_departure_transport_task_id)
            incoming_binding = await self.inbound_binding(
                db, line_id, self.inbound_correlation(original.transport_task_id, target.rack_id)
            )
            if incoming_binding is not None:
                incoming = await self.task_for_binding(db, incoming_binding)
                incoming, incoming_binding = await self.inbound(db, line_id, incoming.transport_task_id)
                if incoming_binding.window_released_at is not None:
                    continue
                if (incoming.request_json["rack_id"], incoming.request_json["target_face"]) != (
                    target.rack_id,
                    target.rack_face,
                ) or (
                    peer.target_inbound_transport_task_id is not None
                    and peer.target_inbound_transport_task_id != incoming.transport_task_id
                ):
                    raise ValueError("shared replacement differs from its frozen ACCEPT")
            elif peer.target_inbound_transport_task_id is not None:
                raise ValueError("shared replacement lost its original incoming binding")
            round_id = binding.correlation_id.removeprefix(ROUND_PREFIX)
            if peer.target_departure_round_id is not None and peer.target_departure_round_id != round_id:
                raise ValueError("shared replacement lost its original round")
            fact = await db.get(InboundEvidence, binding.source_evidence_id)
            rounds.add((round_id, binding.resource_fence_id, fact.operation_id))
        if len(rounds) > 1:
            raise ValueError("target has ambiguous unfinished replacement rounds")
        return next(iter(rounds)) if rounds else None

    async def share_current(self, db, line_id, pickup, observed):
        peers = list(
            (
                await db.scalars(
                    select(AutomaticPickingPickup)
                    .join(AutomaticPickingWork, AutomaticPickingWork.id == AutomaticPickingPickup.work_id)
                    .where(
                        AutomaticPickingWork.workline_id == line_id,
                        AutomaticPickingPickup.target_departure_round_id == observed.inbound_client_request_id,
                        AutomaticPickingPickup.target_departure_confirmation_id.is_not(None),
                    )
                )
            ).all()
        )
        ids = {peer.target_departure_confirmation_id for peer in peers}
        if len(ids) > 1:
            raise ValueError("target departure round has conflicting current requests")
        if peers:
            pickup.target_departure_round_id = observed.inbound_client_request_id
            pickup.target_departure_confirmation_id = peers[0].target_departure_confirmation_id
            pickup.target_departure_retry_at = peers[0].target_departure_retry_at

    async def bind_current(self, db, line_id, pickup, round_id, confirmation_id, retry_at=None):
        await db.execute(
            update(AutomaticPickingPickup)
            .where(
                AutomaticPickingPickup.work_id.in_(
                    select(AutomaticPickingWork.id).where(AutomaticPickingWork.workline_id == line_id)
                ),
                AutomaticPickingPickup.target_departure_round_id == round_id,
            )
            .values(target_departure_confirmation_id=confirmation_id, target_departure_retry_at=retry_at)
        )
        pickup.target_departure_round_id = round_id
        pickup.target_departure_confirmation_id = confirmation_id
        pickup.target_departure_retry_at = retry_at
        await db.flush()

    async def current_input(self, db, line_id, pickup):
        from .target_replacement import MatchedTargetDeparture

        request = await self.request(db, line_id, pickup.target_departure_confirmation_id)
        task = await db.scalar(
            select(TransportTask).where(TransportTask.client_request_id == pickup.target_departure_round_id)
        )
        if task is None:
            return None
        arrival = await db.scalar(
            select(InboundEvidence).where(
                InboundEvidence.source_identity == f"transport:{task.transport_task_id}:outcome:{task.outcome_version}"
            )
        )
        if arrival is None:
            return None
        return MatchedTargetDeparture(
            line_id,
            pickup.material_execution_id,
            pickup.accept_evidence_id,
            request.request_payload["data"]["rack_id"],
            pickup.target_departure_round_id,
            arrival.id,
            request.operation_id,
        )

    async def for_round(self, db, line_id, inbound_client_request_id):
        return await bindings.get_by_decision_identity_for_update(
            db, workline_id=line_id, correlation_id=ROUND_PREFIX + inbound_client_request_id, step=TARGET_DEPARTURE_STEP
        )

    async def transport(self, db, line_id, transport_id):
        task = await db.scalar(
            select(TransportTask)
            .where(TransportTask.transport_task_id == transport_id)
            .execution_options(populate_existing=True)
        )
        binding = await bindings.get_by_client_request_id(db, task.client_request_id) if task else None
        if (
            task is None
            or binding is None
            or binding.workline_id != line_id
            or binding.picking_task_id is not None
            or binding.step != TARGET_DEPARTURE_STEP
            or not binding.correlation_id.startswith(ROUND_PREFIX)
            or not binding.correlation_id.removeprefix(ROUND_PREFIX)
            or task.kind != "RACK_MOVE"
            or task.authority_workline_id != line_id
            or task.caller_json != {"workline_id": str(line_id), "station_id": None}
            or task.request_json.get("rcs_template_id") != "F01"
            or task.request_json.get("rack_id") != binding.resource_fence_id
        ):
            raise ValueError("target replacement lost original F01 identity")
        fact = await db.get(InboundEvidence, binding.source_evidence_id)
        snapshot = await self.decision(db, line_id, fact.operation_id, binding.resource_fence_id) if fact else None
        if (
            snapshot is None
            or snapshot.evidence_id != binding.source_evidence_id
            or task.request_json.get("source") != {"kind": "RACK", "location_code": binding.resource_fence_id}
            or task.request_json.get("target")
            != {
                "kind": snapshot.outcome.result.rack_destination.kind,
                "location_code": snapshot.outcome.result.rack_destination.location_code,
            }
            or task.request_json.get("target_face") is not None
        ):
            raise ValueError("target replacement differs from original departure READY")
        return task, binding

    async def pending(self, db, line_id):
        unpublished = exists(
            select(InboundEvidence.id).where(
                InboundEvidence.transport_task_id == TransportTask.transport_task_id,
                InboundEvidence.kind == "TRANSPORT_RESULT",
                InboundEvidence.published_at.is_(None),
            )
        )
        return list(
            (
                await db.scalars(
                    select(TransportTask.transport_task_id)
                    .join(
                        TransportDecisionBinding,
                        TransportDecisionBinding.client_request_id == TransportTask.client_request_id,
                    )
                    .where(
                        TransportDecisionBinding.workline_id == line_id,
                        TransportDecisionBinding.step.in_((TARGET_DEPARTURE_STEP, TARGET_INBOUND_STEP)),
                        or_(
                            TransportTask.status.not_in(("SUCCEEDED", "FAILED", "REJECTED")),
                            TransportTask.outcome_version > TransportTask.published_outcome_version,
                            unpublished,
                        ),
                    )
                    .order_by(TransportTask.id)
                )
            ).all()
        )

    def matches(self, evidence, task, binding):
        payload = evidence.normalized_payload
        return (
            evidence.kind == "TRANSPORT_RESULT"
            and evidence.apply_status == "APPLIED"
            and evidence.transport_task_id == task.transport_task_id
            and evidence.workline_id == binding.workline_id
            and evidence.picking_task_id is None
            and payload.get("transport_task_id") == task.transport_task_id
            and payload.get("client_request_id") == task.client_request_id
            and payload.get("source_evidence_id") == binding.source_evidence_id
            and payload.get("step") == binding.step
            and binding.step in (TARGET_DEPARTURE_STEP, TARGET_INBOUND_STEP)
            and type(payload.get("outcome_version")) is int
            and 0 < payload["outcome_version"] <= task.outcome_version
            and (
                payload["outcome_version"] != task.outcome_version
                or all(payload.get(key) == value for key, value in task.outcome_json.items())
            )
            and canonical_json_digest(payload) == evidence.payload_digest
        )

    async def validate_outcome(self, db, outcome, binding):
        reader = self.inbound if binding.step == TARGET_INBOUND_STEP else self.transport
        _, original = await reader(db, binding.workline_id, outcome.transport_task_id)
        if original.id != binding.id:
            raise ValueError("target departure outcome binding differs")

    async def finished(self, db, line_id, transport_id):
        task, binding = await self.transport(db, line_id, transport_id)
        evidence = await db.scalar(
            select(InboundEvidence).where(
                InboundEvidence.source_identity == f"transport:{transport_id}:outcome:{task.outcome_version}"
            )
        )
        return (
            task.status == "SUCCEEDED"
            and task.outcome_version > 0
            and task.published_outcome_version == task.outcome_version
            and evidence is not None
            and evidence.published_at is not None
            and self.matches(evidence, task, binding)
        )

    async def inbound_position(self, db, line_id):
        line = await db.get(WorkLine, line_id)
        code = line.position_bindings.get("TRANSFER_RACK", {}).get("location_id")
        position = await db.scalar(
            select(WorkLinePosition).where(
                WorkLinePosition.workline_id == line_id,
                WorkLinePosition.workline_code == line.line_code,
                WorkLinePosition.logic_location_code == code,
                WorkLinePosition.enabled.is_(True),
            )
        )
        return (
            (line, position)
            if position is not None and position.capacity > 0 and position.position_type == "RACK_POSITION"
            else None
        )

    async def inbound_binding(self, db, line_id, correlation_id):
        return await bindings.get_by_decision_identity_for_update(
            db, workline_id=line_id, correlation_id=correlation_id, step=TARGET_INBOUND_STEP
        )

    async def task_for_binding(self, db, binding):
        return await db.scalar(
            select(TransportTask).where(TransportTask.client_request_id == binding.client_request_id)
        )

    async def inbound(self, db, line_id, transport_id):
        task = await db.scalar(
            select(TransportTask)
            .where(TransportTask.transport_task_id == transport_id)
            .execution_options(populate_existing=True)
        )
        binding = await bindings.get_by_client_request_id(db, task.client_request_id) if task else None
        if (
            task is None
            or binding is None
            or binding.workline_id != line_id
            or binding.picking_task_id is not None
            or binding.step != TARGET_INBOUND_STEP
            or not binding.correlation_id.startswith("auto-target-in:")
            or task.kind != "RACK_MOVE"
            or task.authority_workline_id != line_id
            or task.caller_json != {"workline_id": str(line_id), "station_id": None}
            or task.request_json.get("rcs_template_id") != "F01"
            or task.request_json.get("rack_id") != binding.resource_fence_id
            or task.request_json.get("source") != {"kind": "RACK", "location_code": binding.resource_fence_id}
            or task.request_json.get("target")
            != {"kind": "RACK_POSITION", "location_code": binding.window_target_location_code}
        ):
            raise ValueError("replacement inbound lost original F01 identity")
        fact = await db.get(InboundEvidence, binding.source_evidence_id)
        snapshot = (
            await MaterialDecideResultReader().by_operation_id(
                db, material_execution_id=fact.material_execution_id, operation_id=fact.operation_id
            )
            if fact
            else None
        )
        if (
            fact is None
            or fact.workline_id != line_id
            or fact.published_at is None
            or canonical_json_digest(fact.normalized_payload) != fact.payload_digest
            or snapshot is None
            or snapshot.status != "COMPLETED"
            or snapshot.published_at is None
            or snapshot.evidence_id != binding.source_evidence_id
            or type(snapshot.outcome.result) is not PickingMaterialAccept
            or type(snapshot.outcome.result.target_preparation) is not PickingTargetReplace
            or snapshot.outcome.result.target_locator.rack_id != binding.resource_fence_id
            or task.request_json.get("target_face") != snapshot.outcome.result.target_locator.rack_face
        ):
            raise ValueError("replacement inbound differs from frozen ACCEPT")
        return task, binding

    async def inbound_ready(self, db, line_id, transport_id, target):
        task, binding = await self.inbound(db, line_id, transport_id)
        if (
            task.status != "SUCCEEDED"
            or task.outcome_version < 1
            or task.published_outcome_version != task.outcome_version
        ):
            return False
        fact = await db.scalar(
            select(InboundEvidence).where(
                InboundEvidence.source_identity == f"transport:{transport_id}:outcome:{task.outcome_version}"
            )
        )
        if fact is None or fact.published_at is None or not self.matches(fact, task, binding):
            return False
        await bindings.lock_object_authority(db, object_type="RACK", object_id=target.rack_id)
        position = await db.scalar(
            select(PositionProjection)
            .where(PositionProjection.object_type == "RACK", PositionProjection.object_id == target.rack_id)
            .execution_options(populate_existing=True)
        )
        members = fact.normalized_payload.get("members", [])
        return (
            binding.window_released_at is None
            and binding.resource_fence_id == target.rack_id
            and task.request_json.get("target_face") == target.rack_face
            and len(members) == 1
            and members[0].get("object_id") == target.rack_id
            and members[0].get("final_position") == task.request_json["target"]
            and members[0].get("arrival_face") == target.rack_face
            and not members[0].get("position_unknown")
            and position is not None
            and position.workline_id == line_id
            and not position.position_unknown
            and position.position_json == task.request_json["target"]
            and position.arrival_face == target.rack_face
            and position.source_transport_task_id == transport_id
            and position.source_causal_token == binding.causal_token
            and position.source_effect_phase == "FINAL_RESULT"
        )
