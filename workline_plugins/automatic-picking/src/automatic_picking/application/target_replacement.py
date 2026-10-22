"""REPLACE departure 决定、WAIT 续问及原 F01 换架；目标就绪交给共用 PUT。"""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from wes_plugin_sdk import (
    PickingMaterialAccept,
    PickingTargetReplace,
    RackDepartureReady,
    RackDepartureWait,
    TransportRackPosition,
    TransportRackReference,
    TransportRcsTemplateId,
    wms_operations,
)

from src.core.uuid7 import new_uuid7
from src.utils.canonical_json import canonical_json_digest
from src.utils.timezone import timezone

from .pickup_model import AutomaticPickingPickup
from .pickup_repository import PickupRepository
from .target_preparation import ReadyTarget
from .target_replacement_repository import (
    ROUND_PREFIX,
    TARGET_DEPARTURE_STEP,
    TARGET_INBOUND_STEP,
    TargetReplacementRepository,
)


@dataclass(frozen=True, slots=True)
class MatchedTargetDeparture:
    workline_id: int
    material_execution_id: int
    accept_evidence_id: int
    old_rack_id: str
    inbound_client_request_id: str
    arrival_evidence_id: int
    departure_operation_id: str | None = None

    def __post_init__(self):
        if (
            any(
                type(v) is not int or v <= 0
                for v in (
                    self.workline_id,
                    self.material_execution_id,
                    self.accept_evidence_id,
                    self.arrival_evidence_id,
                )
            )
            or any(
                type(v) is not str or not v.strip() or len(v) > limit
                for v, limit in (
                    (self.old_rack_id, 100),
                    (self.inbound_client_request_id, 120),
                )
            )
            or (
                self.departure_operation_id is not None
                and (
                    type(self.departure_operation_id) is not str
                    or not self.departure_operation_id.strip()
                    or len(self.departure_operation_id) > 120
                )
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
    def __init__(self, creator, reader, *, repository=None, scheduler=None, clock=None):
        self.creator, self.reader = creator, reader
        self.scheduler, self.clock = scheduler, clock or timezone.now_for_db
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
        if pickup.target_departure_confirmation_id is not None:
            request = await self.repository.request(db, work.workline_id, pickup.target_departure_confirmation_id)
            retained = (
                pickup.target_departure_round_id,
                request.request_payload["data"]["rack_id"],
                request.operation_id,
            )
        else:
            retained = await self.repository.shared_target_round(db, work.workline_id, pickup, accepted.target_locator)
        if retained is not None and await self._restore_round(db, work, pickup, *retained):
            return 1
        observed = (
            await self.repository.current_input(db, work.workline_id, pickup)
            if pickup.target_departure_confirmation_id is not None
            else await self.reader.read_in_session(
                db, workline_id=work.workline_id, material_execution_id=execution.id, target=accepted.target_locator
            )
        )
        if observed is None or type(observed) is ReadyTarget:
            return 0
        if (
            type(observed) is not MatchedTargetDeparture
            or observed.workline_id != work.workline_id
            or observed.material_execution_id != execution.id
            or observed.accept_evidence_id != pickup.accept_evidence_id
            or observed.old_rack_id == accepted.target_locator.rack_id
        ):
            raise ValueError("REPLACE departure input differs from this ACCEPT")
        if await self._restore_round(
            db, work, pickup, observed.inbound_client_request_id, observed.old_rack_id, observed.departure_operation_id
        ):
            return 1
        snapshot, changed = await self._decide(db, work, pickup, observed)
        if snapshot is None:
            return changed
        observed = MatchedTargetDeparture(
            observed.workline_id,
            observed.material_execution_id,
            observed.accept_evidence_id,
            observed.old_rack_id,
            observed.inbound_client_request_id,
            observed.arrival_evidence_id,
            snapshot.intent.operation_id,
        )
        if not await self.repository.current_arrival(db, work.workline_id, observed, snapshot):
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

    async def _restore_round(self, db, work, pickup, round_id, rack_id, operation_id):
        original = await self.repository.for_round(db, work.workline_id, round_id)
        if original is None:
            return False
        task = await self.repository.task_for_binding(db, original)
        task, binding = await self.repository.transport(db, work.workline_id, task.transport_task_id)
        from src.app.execution.models import InboundEvidence

        fact = await db.get(InboundEvidence, binding.source_evidence_id)
        if binding.resource_fence_id != rack_id or (operation_id is not None and fact.operation_id != operation_id):
            raise ValueError("REPLACE round already owns another frozen departure")
        request = await self.repository.operation_request(db, work.workline_id, fact.operation_id)
        # 已冻结同轮义务先恢复；接纳后的离位/待核投影不撤销原执行身份。
        await self.repository.bind_current(db, work.workline_id, pickup, round_id, request.id)
        pickup.target_departure_transport_task_id = task.transport_task_id
        await db.flush()
        return True

    async def _decide(self, db, work, pickup, observed):
        line_id = work.workline_id
        context = await self.repository.current_context(db, line_id, observed)
        if context is None:
            return None, 0
        if pickup.target_departure_confirmation_id is None:
            await self.repository.share_current(db, line_id, pickup, observed)
        if pickup.target_departure_confirmation_id is None and observed.departure_operation_id is not None:
            snapshot = await self.repository.decision(
                db, line_id, observed.departure_operation_id, observed.old_rack_id
            )
            if snapshot is None or not await self.repository.current_arrival(db, line_id, observed, snapshot):
                return None, 0
            request = await self.repository.operation_request(db, line_id, observed.departure_operation_id)
            await self.repository.bind_current(db, line_id, pickup, observed.inbound_client_request_id, request.id)
            return snapshot, 0
        if pickup.target_departure_confirmation_id is None:
            return None, await self._request(db, work, pickup, observed, context)
        request = await self.repository.request(db, line_id, pickup.target_departure_confirmation_id)
        if request.status == "COMPLETED":
            from src.app.execution.models import InboundEvidence

            fact = await db.get(InboundEvidence, request.response_evidence_id)
            if fact is None or fact.published_at is None:
                return None, 0
        snapshot = await self.repository.reader.by_operation_id(
            db, workline_id=line_id, operation_id=request.operation_id, rack_id=observed.old_rack_id
        )
        if (snapshot.intent.current_location, snapshot.intent.current_face) != context:
            raise ValueError("target departure current position differs from frozen request")
        if snapshot.status != "COMPLETED":
            return None, 0
        if type(snapshot.outcome.result) is RackDepartureWait:
            changed = await self._remember_wait(db, work.workline_id, pickup, snapshot)
            if self.clock() >= pickup.target_departure_retry_at:
                changed += await self._request(db, work, pickup, observed, context)
            return None, changed
        return (snapshot if type(snapshot.outcome.result) is RackDepartureReady else None), 0

    async def _request(self, db, work, pickup, observed, context):
        if self.scheduler is None:
            return 0
        location, face = context
        intent = wms_operations.outbound_rack_departure_decide(
            operation_id=new_uuid7(),
            task_id=None,
            rack_id=observed.old_rack_id,
            current_location=location,
            current_face=face,
        )
        await self.scheduler.create_in_session(db, intent, workline_id=work.workline_id, created_at=self.clock())
        request = await self.repository.operation_request(db, work.workline_id, intent.operation_id)
        await self.repository.bind_current(db, work.workline_id, pickup, observed.inbound_client_request_id, request.id)
        return 1

    async def _remember_wait(self, db, line_id, pickup, snapshot):
        from src.app.execution.models import InboundEvidence

        fact = await db.get(InboundEvidence, snapshot.evidence_id)
        if (
            fact is None
            or fact.workline_id != line_id
            or fact.picking_task_id is not None
            or canonical_json_digest(fact.normalized_payload) != fact.payload_digest
        ):
            raise ValueError("target departure WAIT lost original evidence owner or digest")
        due = fact.received_at + timedelta(milliseconds=snapshot.outcome.result.retry_after_ms)
        if pickup.target_departure_retry_at is not None:
            if pickup.target_departure_retry_at != due:
                raise ValueError("target departure WAIT deadline differs from original result")
            return 0
        await self.repository.bind_current(
            db, line_id, pickup, pickup.target_departure_round_id, pickup.target_departure_confirmation_id, due
        )
        return 1

    async def apply_decision_result_in_session(self, db, *, line, evidence):
        pickup = await self.repository.by_decision(db, line.id, evidence.operation_id)
        if pickup is None:
            return "TARGET_DEPARTURE_OLD_DECISION"
        request = await self.repository.request(db, line.id, pickup.target_departure_confirmation_id)
        snapshot = await self.repository.reader.for_evidence(
            db,
            workline_id=line.id,
            operation_id=request.operation_id,
            rack_id=request.request_payload["data"]["rack_id"],
            evidence_id=evidence.id,
        )
        if type(snapshot.outcome.result) is RackDepartureWait:
            await self._remember_wait(db, line.id, pickup, snapshot)
        return "TARGET_DEPARTURE_DECIDED"

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
        correlation = self.repository.inbound_correlation(old.transport_task_id, target.rack_id)
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
        if pickup is None:
            return None
        if pickup.target_inbound_transport_task_id is None:
            observed = await self.reader.read_in_session(
                db, workline_id=workline_id, material_execution_id=material_execution_id, target=target
            )
            return observed if type(observed) is ReadyTarget else None
        work = await PickupRepository.owner(db, pickup)
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
