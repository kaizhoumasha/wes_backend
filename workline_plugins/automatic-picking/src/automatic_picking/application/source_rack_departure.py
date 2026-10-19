"""普通来源架按精确原 READY 离场；本入口不创建 WMS 决定。"""

from dataclasses import dataclass

from src.utils.timezone import timezone

from .source_rack_departure_repository import DEPARTURE_STEP, SourceRackDepartureRepository


@dataclass(frozen=True, slots=True)
class MatchedSourceRackDeparture:
    motion_id: int
    operation_id: str

    def __post_init__(self):
        if (
            type(self.motion_id) is not int
            or not 0 < self.motion_id <= 2**63 - 1
            or type(self.operation_id) is not str
            or not self.operation_id.strip()
            or len(self.operation_id) > 120
        ):
            raise ValueError("source departure requires exact current identities")


class SourceRackDepartureFlow:
    def __init__(self, returns, creator, reader, *, departure_reader=None, repository=None, clock=None):
        self.returns, self.creator, self.reader = returns, creator, reader
        self.repository = repository or SourceRackDepartureRepository(returns.motions, departure_reader)
        self.clock = clock or timezone.now_for_db

    async def advance_in_session(self, db, line):
        count = 0
        for current in await self.repository.pending(db, line.id):
            if await self.repository.finished(db, current):
                current.archived_at = self.clock()
                await db.flush()
                count += 1
        intents = await self.reader.read_in_session(db, workline_id=line.id) if self.reader else None
        if intents is not None and type(intents) is not tuple:
            raise TypeError("departure requires immutable explicit inputs")
        for intent in intents or ():
            if type(intent) is not MatchedSourceRackDeparture:
                raise TypeError("departure requires typed explicit inputs")
            current = await self.repository.by_id(db, intent.motion_id)
            if current is None or current.workline_id != line.id:
                raise ValueError("departure lost exact current motion stage")
            if current.archived_at is not None:
                continue
            if current.departure_transport_task_id is not None:
                if current.departure_operation_id != intent.operation_id:
                    raise ValueError("departure frozen operation drift")
                await self.repository.transport(db, current)
                continue
            if current.closed_at is None or current.inbound_client_request_id is None or not line.is_active:
                continue
            snapshot = await self.repository.ready(db, line, current, intent.operation_id)
            if snapshot is None:
                continue
            if not await self.repository.service_finished(db, current):
                continue
            blocked = False
            for allocation in await self.returns.repository.current(db, line.id):
                if (
                    allocation.rack_id == current.rack_id
                    and allocation.created_at >= current.round_started_at
                    and not await self.returns._motion_obligation_closed(db, allocation)
                ):
                    blocked = True
                    break
            if blocked:
                continue
            handle = await self.creator.create_source_return(
                db,
                workline_id=line.id,
                source_evidence_id=snapshot.evidence_id,
                correlation_id="departure:" + intent.operation_id,
                step=DEPARTURE_STEP,
                rack_id=current.rack_id,
                destination=snapshot.outcome.result.rack_destination,
            )
            await self.repository.bind(db, current, intent.operation_id, handle.transport_task_id)
            count += 1
        return count

    async def apply_transport_result_in_session(self, db, *, line, evidence):
        return await self.repository.observe(db, line, evidence)
