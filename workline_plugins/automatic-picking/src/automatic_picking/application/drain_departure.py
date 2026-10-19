"""终面服务后的显式 READY 消费；首次 departure 和 WAIT 仍未接入。"""

from dataclasses import dataclass

from sqlalchemy import select
from wes_plugin_sdk import RackDepartureReady, TransportRackPosition

from src.app.execution.models import InboundEvidence, TransportDecisionBinding, WmsConfirmation
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
    def __init__(self, rotation, reader, *, clock=timezone.now_for_db):
        self.rotation, self.reader, self.clock = rotation, reader, clock
        self.repository = DrainDepartureRepository()

    async def recover_in_session(self, db, line):
        count = 0
        for stage in await self.repository.pending(db, line.id):
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
            if await self.repository.finished(db, stage):
                stage.archived_at = self.clock()
                await db.flush()
                count += 1
        return count

    async def advance_in_session(self, db, line, root, ready):
        if not line.is_active:
            return 0
        intents = await self.reader.read_in_session(db, workline_id=line.id)
        if intents is not None and type(intents) is not tuple:
            raise TypeError("drain departure requires immutable explicit input")
        rows = {r.rack_id: r for r in await self.rotation.repository.racks(db, root.id)}
        phases, count = await self.rotation.returns.motions.current(db, line.id), 0
        for intent in intents or ():
            if type(intent) is not MatchedDrainDeparture:
                raise TypeError("drain departure requires typed explicit input")
            if intent.drain_id != root.id:
                continue
            row = rows.get(intent.rack_id)
            if row is None or row.arrival_evidence_id != intent.arrival_evidence_id:
                continue
            stage = next(
                (p for p in phases if p.rack_id == row.rack_id and p.arrival_evidence_id == intent.arrival_evidence_id),
                None,
            )
            if stage is None or stage.closed_at is None or stage.departure_operation_id is not None:
                continue
            if stage.source_evidence_id != root.response_evidence_id or stage.inbound_client_request_id is not None:
                raise ValueError("drain departure stage differs from original reservation")
            arrival = await self.rotation.repository.current_arrival(db, line, row)
            if arrival is None:
                continue
            task, fact = arrival
            faces = next(r.rack_faces for r in ready.racks if r.rack_id == row.rack_id)
            if task.request_json["target_face"] != faces[-1]:
                continue
            binding = await db.get(TransportDecisionBinding, row.binding_id)
            if await self.rotation._newer_action(db, row, binding):
                continue
            direct = [
                a
                for a in await self.rotation.returns.repository.current(db, line.id)
                if self.rotation.returns._motion_allocation_matches(a, stage, (root, ready))
            ]
            if any([not await self.rotation.returns._motion_obligation_closed(db, a) for a in direct]):
                continue
            request = await db.scalar(
                select(WmsConfirmation).where(
                    WmsConfirmation.operation == "outbound.rack.departure_decide@v1",
                    WmsConfirmation.operation_id == intent.operation_id,
                )
            )
            if request is None or request.status != "COMPLETED":
                continue
            snapshot = await self.repository.reader.for_evidence(
                db,
                workline_id=line.id,
                operation_id=intent.operation_id,
                rack_id=row.rack_id,
                evidence_id=request.response_evidence_id,
            )
            if type(snapshot.outcome.result) is not RackDepartureReady:
                continue
            evidence = await db.get(InboundEvidence, snapshot.evidence_id)
            if evidence.published_at is None:
                continue
            position = task.request_json["position" if task.kind == "RACK_ROTATE" else "target"]
            if (
                snapshot.intent.task_id is not None
                or snapshot.intent.current_location != TransportRackPosition(position["location_code"])
                or snapshot.intent.current_face != faces[-1]
                or request.created_at < max(fact.received_at, stage.closed_at)
            ):
                raise ValueError("drain departure READY differs from exact completed face")
            handle = await self.rotation.creator.create_source_return(
                db,
                workline_id=line.id,
                source_evidence_id=evidence.id,
                correlation_id="departure:" + intent.operation_id,
                step=DRAIN_DEPARTURE_STEP,
                rack_id=row.rack_id,
                destination=snapshot.outcome.result.rack_destination,
            )
            stage.departure_operation_id, stage.departure_transport_task_id = (
                intent.operation_id,
                handle.transport_task_id,
            )
            await db.flush()
            count += 1
        return count

    async def apply_transport_result_in_session(self, db, *, line, evidence):
        return await self.repository.observe(db, line, evidence)
