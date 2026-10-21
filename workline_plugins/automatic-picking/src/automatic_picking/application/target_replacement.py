"""REPLACE 原旧架交接后创建新架 F01，停在目标就绪，不接 PUT。"""

from dataclasses import dataclass

from sqlalchemy import select
from wes_plugin_sdk import (
    PickingMaterialAccept,
    PickingTargetReplace,
    TransportRackPosition,
    TransportRackReference,
    TransportRcsTemplateId,
)

from src.utils.canonical_json import canonical_json_digest

from .pickup_model import AutomaticPickingPickup
from .target_preparation import ReadyTarget
from .target_replacement_repository import (
    ROUND_PREFIX,
    TARGET_DEPARTURE_STEP,
    TARGET_INBOUND_STEP,
    TargetReplacementRepository,
)
from .work_model import AutomaticPickingWork


@dataclass(frozen=True, slots=True)
class MatchedTargetDeparture:
    workline_id: int
    material_execution_id: int
    accept_evidence_id: int
    old_rack_id: str
    inbound_client_request_id: str
    arrival_evidence_id: int
    departure_operation_id: str

    def __post_init__(self):
        if any(
            type(v) is not int or v <= 0
            for v in (self.workline_id, self.material_execution_id, self.accept_evidence_id, self.arrival_evidence_id)
        ) or any(
            type(v) is not str or not v.strip() or len(v) > limit
            for v, limit in (
                (self.old_rack_id, 100),
                (self.inbound_client_request_id, 120),
                (self.departure_operation_id, 120),
            )
        ):
            raise ValueError("REPLACE departure requires exact current identities")


@dataclass(frozen=True, slots=True)
class ReplacementInboundIntent:
    rack_id: str
    target: TransportRackPosition
    target_face: str
    rcs_template_id: TransportRcsTemplateId = TransportRcsTemplateId.F01

    @property
    def source(self):
        return TransportRackReference(self.rack_id)


class AutomaticPickingTargetReplacementFlow:
    def __init__(self, creator, reader, *, repository=None):
        self.creator, self.reader = creator, reader
        self.repository = repository or TargetReplacementRepository()

    async def recover_in_session(self, db, *, line_id):
        # 已创建义务不再依赖 Pickup、旧进场历史或当前映射。
        for transport_id in await self.repository.pending(db, line_id):
            from src.app.execution.repositories import transport_decision_binding_repository as bindings
            from src.app.transport.models import TransportTask

            task = await db.scalar(select(TransportTask).where(TransportTask.transport_task_id == transport_id))
            binding = await bindings.get_by_client_request_id(db, task.client_request_id)
            reader = self.repository.inbound if binding.step == TARGET_INBOUND_STEP else self.repository.transport
            await reader(db, line_id, transport_id)

    async def prepare_in_session(self, db, *, work, pickup, execution, accepted):
        if pickup.target_inbound_transport_task_id is not None:
            task, _ = await self.repository.inbound(db, work.workline_id, pickup.target_inbound_transport_task_id)
            if (
                task.request_json["rack_id"] != accepted.target_locator.rack_id
                or task.request_json["target_face"] != accepted.target_locator.rack_face
            ):
                raise ValueError("linked replacement target differs from this ACCEPT")
            return 0
        if pickup.target_departure_transport_task_id is not None:
            return await self._inbound_in_session(db, work, pickup, accepted)
        observed = await self.reader.read_in_session(
            db, workline_id=work.workline_id, material_execution_id=execution.id, target=accepted.target_locator
        )
        if observed is None:
            return 0
        if (
            type(observed) is not MatchedTargetDeparture
            or observed.workline_id != work.workline_id
            or observed.material_execution_id != execution.id
            or observed.accept_evidence_id != pickup.accept_evidence_id
            or observed.old_rack_id == accepted.target_locator.rack_id
        ):
            raise ValueError("REPLACE departure input differs from this ACCEPT")
        original = await self.repository.for_round(db, work.workline_id, observed.inbound_client_request_id)
        if original is not None:
            from sqlalchemy import select

            from src.app.transport.models import TransportTask

            transport_id = await db.scalar(
                select(TransportTask.transport_task_id).where(
                    TransportTask.client_request_id == original.client_request_id
                )
            )
            _, binding = await self.repository.transport(db, work.workline_id, transport_id)
            fact = await self.repository.decision(
                db, work.workline_id, observed.departure_operation_id, observed.old_rack_id
            )
            if fact is None or fact.evidence_id != binding.source_evidence_id:
                raise ValueError("REPLACE round already owns another frozen departure")
            if not await self.repository.current_arrival(db, work.workline_id, observed, fact):
                return 0
        else:
            snapshot = await self.repository.decision(
                db, work.workline_id, observed.departure_operation_id, observed.old_rack_id
            )
            if snapshot is None or not await self.repository.current_arrival(db, work.workline_id, observed, snapshot):
                return 0
            handle = await self.creator.create_transfer_departure(
                db,
                workline_id=work.workline_id,
                source_evidence_id=snapshot.evidence_id,
                operation_id=ROUND_PREFIX + observed.inbound_client_request_id,
                step=TARGET_DEPARTURE_STEP,
                rack_id=observed.old_rack_id,
                destination=snapshot.outcome.result.rack_destination,
            )
            transport_id = handle.transport_task_id
        pickup.target_departure_transport_task_id = transport_id
        await db.flush()
        return 1

    async def handed_off_in_session(self, db, *, line_id, transport_id):
        task, _ = await self.repository.transport(db, line_id, transport_id)
        return (
            task.status == "ACCEPTED"
            or task.result_deadline_at is not None
            or await self.repository.finished(db, line_id, transport_id)
        )

    async def apply_transport_result_in_session(self, db, *, line, evidence):
        reader = (
            self.repository.inbound
            if evidence.normalized_payload.get("step") == TARGET_INBOUND_STEP
            else self.repository.transport
        )
        task, binding = await reader(db, line.id, evidence.transport_task_id)
        return (
            "TARGET_REPLACEMENT_DEPARTURE_OBSERVED"
            if self.repository.matches(evidence, task, binding)
            else "RECONCILING"
        )

    async def _inbound_in_session(self, db, work, pickup, accepted):
        old, _ = await self.repository.transport(db, work.workline_id, pickup.target_departure_transport_task_id)
        if old.status != "ACCEPTED" and not await self.repository.finished(db, work.workline_id, old.transport_task_id):
            return 0
        target = accepted.target_locator
        if target.rack_id == old.request_json["rack_id"]:
            raise ValueError("REPLACE requires a new target rack")
        correlation = "auto-target-in:" + canonical_json_digest(
            {"departure": old.transport_task_id, "rack": target.rack_id}
        )
        binding = await self.repository.inbound_binding(db, work.workline_id, correlation)
        if binding is None:
            observed = await self.repository.inbound_position(db, work.workline_id)
            if observed is None:
                return 0
            line, position = observed
            admitted = await self.creator.create_windowed_inbound(
                db,
                workline_id=line.id,
                workline_code=line.line_code,
                picking_task_id=None,
                source_evidence_id=pickup.accept_evidence_id,
                correlation_id=correlation,
                step=TARGET_INBOUND_STEP,
                resource_fence_id=target.rack_id,
                intent=ReplacementInboundIntent(
                    target.rack_id, TransportRackPosition(position.logic_location_code), target.rack_face
                ),
            )
            if admitted == "PENDING":
                return 0
            binding = await self.repository.inbound_binding(db, work.workline_id, correlation)
            if binding is None:
                if admitted == "REUSED":
                    return 0
                raise ValueError("new target F01 lacks its frozen round binding")
        task = await self.repository.task_for_binding(db, binding)
        task, binding = await self.repository.inbound(db, work.workline_id, task.transport_task_id)
        if binding.window_released_at is not None:
            return 0
        if task.request_json["target_face"] != target.rack_face:
            raise ValueError("shared replacement arrival face differs from ACCEPT")
        pickup.target_inbound_transport_task_id = task.transport_task_id
        await db.flush()
        return 1

    async def read_in_session(self, db, *, workline_id, material_execution_id, target):
        from src.app.wms_integration.outbound_picking.services import MaterialDecideResultReader

        pickup = await db.scalar(
            select(AutomaticPickingPickup).where(AutomaticPickingPickup.material_execution_id == material_execution_id)
        )
        if pickup is None or pickup.target_inbound_transport_task_id is None:
            return None
        work = await db.get(AutomaticPickingWork, pickup.work_id)
        if work.workline_id != workline_id:
            raise ValueError("replacement ready material belongs to another WorkLine")
        from src.app.execution.models import InboundEvidence

        fact = await db.get(InboundEvidence, pickup.accept_evidence_id)
        snapshot = (
            await MaterialDecideResultReader().by_operation_id(
                db, material_execution_id=material_execution_id, operation_id=fact.operation_id
            )
            if fact
            else None
        )
        if (
            snapshot is None
            or snapshot.status != "COMPLETED"
            or snapshot.published_at is None
            or snapshot.evidence_id != pickup.accept_evidence_id
            or type(snapshot.outcome.result) is not PickingMaterialAccept
            or type(snapshot.outcome.result.target_preparation) is not PickingTargetReplace
            or snapshot.outcome.result.target_locator != target
        ):
            return None
        if await self.repository.inbound_ready(db, workline_id, pickup.target_inbound_transport_task_id, target):
            return ReadyTarget(workline_id, material_execution_id, target)
        return None
