"""终面 departure 的当前请求与原执行；业务恢复由既有 batch 承担。"""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from wes_plugin_sdk import (
    RackDepartureReady,
    RackDepartureWait,
    ReturnBufferDrainReady,
    TransportRackPosition,
    wms_operations,
)

from src.app.execution.models import InboundEvidence, TransportDecisionBinding, WmsConfirmation
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone

from .drain_departure_repository import DRAIN_DEPARTURE_STEP, DrainDepartureRepository


@dataclass(frozen=True, slots=True)
class MatchedDrainDeparture:
    drain_id: int
    rack_id: str
    arrival_evidence_id: int
    operation_id: str

    def __post_init__(self):
        if any(type(i) is not int or not 0 < i <= 2**63 - 1 for i in (self.drain_id, self.arrival_evidence_id)) or any(
            type(s) is not str or not s.strip() for s in (self.rack_id, self.operation_id)
        ):
            raise ValueError("drain departure requires exact original identities")


class DrainDepartureFlow:
    def __init__(self, rotation, reader=None, *, scheduler=None, clock=timezone.now_for_db, uuid_factory=new_uuid7):
        self.rotation, self.reader, self.clock = rotation, reader, clock
        self.scheduler, self.uuid_factory = scheduler, uuid_factory
        self.repository = DrainDepartureRepository()

    async def recover_in_session(self, db, line):
        count = 0
        for stage in await self.repository.pending(db, line.id):
            if stage.departure_transport_task_id is None:
                count += await self._recover_decision(db, line, stage)
                continue
            task, _, _ = await self.repository.transport(db, stage)
            fact = await db.scalar(
                select(InboundEvidence).where(
                    InboundEvidence.kind == "TRANSPORT_RESULT",
                    InboundEvidence.source_identity
                    == f"transport:{task.transport_task_id}:outcome:{task.outcome_version}",
                )
            )
            if (
                fact is not None
                and fact.published_at is not None
                and await self.repository.observe(db, line, fact) == "RECONCILING"
            ):
                raise ValueError("drain departure result differs from original action")
            if await self.repository.rejected(db, stage):
                context = await self._current_context(db, line, stage)
                if context is not None:
                    count += await self._request(db, line, stage, context)
                    continue
            if await self.repository.finished(db, stage):
                stage.archived_at = self.clock()
                await db.flush()
                count += 1
        return count

    async def _recover_decision(self, db, line, stage):
        snapshot = await self.repository.decision(db, stage)
        if snapshot is None or snapshot.status != "COMPLETED":
            return 0
        context = await self._current_context(db, line, stage)
        if type(snapshot.outcome.result) is RackDepartureWait:
            changed = await self._remember_wait(db, stage, snapshot)
            if context is not None and self.clock() >= stage.departure_retry_at:
                return changed + await self._request(db, line, stage, context)
            return changed
        if context is None:
            return 0
        return await self._create_transport(db, line, stage, context, stage.departure_operation_id)

    async def _current_context(self, db, line, stage):
        current = await self.rotation.returns.drains.current(db, line)
        if (
            current is not None
            and type(current[1]) is ReturnBufferDrainReady
            and current[0].response_evidence_id == stage.source_evidence_id
        ):
            root, ready = current
            row = next(
                (r for r in await self.rotation.repository.racks(db, root.id) if r.rack_id == stage.rack_id), None
            )
            if row is not None:
                return await self._context(db, line, root, ready, row, stage)
        return None

    async def _remember_wait(self, db, stage, snapshot):
        fact = await db.get(InboundEvidence, snapshot.evidence_id)
        due = fact.received_at + timedelta(milliseconds=snapshot.outcome.result.retry_after_ms)
        if stage.departure_retry_at is not None:
            if stage.departure_retry_at != due:
                raise ValueError("drain departure WAIT deadline drift")
            return 0
        stage.departure_retry_at = due
        await db.flush()
        return 1

    async def apply_decision_result_in_session(self, db, *, line, evidence):
        stage = await self.repository.by_decision(db, line.id, evidence.operation_id)
        if stage is None or stage.archived_at is not None:
            return "DRAIN_DEPARTURE_OLD_DECISION"
        snapshot = await self.repository.decision(db, stage, evidence=evidence)
        if type(snapshot.outcome.result) is RackDepartureWait:
            await self._remember_wait(db, stage, snapshot)
        return "DRAIN_DEPARTURE_DECIDED"

    async def advance_in_session(self, db, line, root, ready):
        if not line.is_active:
            return 0
        intents = await self.reader.read_in_session(db, workline_id=line.id) if self.reader is not None else ()
        if intents is not None and type(intents) is not tuple:
            raise TypeError("drain departure requires immutable explicit input")
        for intent in intents or ():
            if type(intent) is not MatchedDrainDeparture:
                raise TypeError("drain departure requires typed explicit input")
        phases, count = await self.rotation.returns.motions.current(db, line.id), 0
        for row in await self.rotation.repository.racks(db, root.id):
            stage = next(
                (p for p in phases if p.rack_id == row.rack_id and p.arrival_evidence_id == row.arrival_evidence_id),
                None,
            )
            if stage is None or stage.departure_operation_id is not None:
                continue
            context = await self._context(db, line, root, ready, row, stage)
            if context is None:
                continue
            explicit = [
                i
                for i in intents or ()
                if (i.drain_id, i.rack_id, i.arrival_evidence_id) == (root.id, row.rack_id, row.arrival_evidence_id)
            ]
            if len({i.operation_id for i in explicit}) > 1:
                raise ValueError("drain stage has conflicting departure inputs")
            if explicit:
                count += await self._create_transport(db, line, stage, context, explicit[0].operation_id)
            else:
                count += await self._request(db, line, stage, context)
        return count

    async def _context(self, db, line, root, ready, row, stage):
        if (
            not line.is_active
            or root.archived_at is not None
            or stage.archived_at is not None
            or stage.closed_at is None
            or row.arrival_evidence_id != stage.arrival_evidence_id
        ):
            return None
        if stage.source_evidence_id != root.response_evidence_id or stage.inbound_client_request_id is not None:
            raise ValueError("drain departure stage differs from original reservation")
        arrival = await self.rotation.repository.current_arrival(db, line, row)
        if arrival is None:
            return None
        task, fact = arrival
        faces = next(r.rack_faces for r in ready.racks if r.rack_id == row.rack_id)
        if task.request_json["target_face"] != faces[-1]:
            return None
        binding = await db.get(TransportDecisionBinding, row.binding_id)
        if await self.rotation._newer_action(db, row, binding):
            return None
        direct = [
            a
            for a in await self.rotation.returns.repository.current(db, line.id)
            if self.rotation.returns._motion_allocation_matches(a, stage, (root, ready))
        ]
        if any([not await self.rotation.returns._motion_obligation_closed(db, a) for a in direct]):
            return None
        position = task.request_json["position" if task.kind == "RACK_ROTATE" else "target"]
        return TransportRackPosition(position["location_code"]), faces[-1], fact

    async def _request(self, db, line, stage, context):
        if self.scheduler is None:
            return 0
        location, face, _fact = context
        operation_id = self.uuid_factory()
        intent = wms_operations.outbound_rack_departure_decide(
            operation_id=operation_id,
            task_id=None,
            rack_id=stage.rack_id,
            current_location=location,
            current_face=face,
        )
        await self.scheduler.create_in_session(db, intent, workline_id=line.id, created_at=self.clock())
        stage.departure_operation_id, stage.departure_retry_at = operation_id, None
        stage.departure_transport_task_id = stage.departure_result_evidence_id = None
        await db.flush()
        return 1

    async def _create_transport(self, db, line, stage, context, operation_id):
        request = await db.scalar(
            select(WmsConfirmation).where(
                WmsConfirmation.operation == "outbound.rack.departure_decide@v1",
                WmsConfirmation.operation_id == operation_id,
            )
        )
        if request is None or request.status != "COMPLETED":
            return 0
        snapshot = await self.repository.reader.for_evidence(
            db,
            workline_id=line.id,
            operation_id=operation_id,
            rack_id=stage.rack_id,
            evidence_id=request.response_evidence_id,
        )
        if type(snapshot.outcome.result) is not RackDepartureReady:
            return 0
        evidence = await db.get(InboundEvidence, snapshot.evidence_id)
        if evidence.published_at is None:
            return 0
        location, face, arrival = context
        if (
            snapshot.intent.task_id is not None
            or snapshot.intent.current_location != location
            or snapshot.intent.current_face != face
            or request.created_at < max(arrival.received_at, stage.closed_at)
        ):
            raise ValueError("drain departure READY differs from exact completed face")
        handle = await self.rotation.creator.create_source_return(
            db,
            workline_id=line.id,
            source_evidence_id=evidence.id,
            correlation_id="departure:" + operation_id,
            step=DRAIN_DEPARTURE_STEP,
            rack_id=stage.rack_id,
            destination=snapshot.outcome.result.rack_destination,
        )
        stage.departure_operation_id, stage.departure_transport_task_id = operation_id, handle.transport_task_id
        stage.departure_retry_at = None
        await db.flush()
        return 1

    async def apply_transport_result_in_session(self, db, *, line, evidence):
        return await self.repository.observe(db, line, evidence)
