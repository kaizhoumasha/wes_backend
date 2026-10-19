"""普通来源架按精确原 READY 离场；当前请求与 WAIT 由既有 batch 持久恢复。"""

from dataclasses import dataclass
from datetime import timedelta

from wes_plugin_sdk import RackDepartureWait, wms_operations

from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone

from .source_rack_departure_repository import DEPARTURE_STEP, SourceRackDepartureRepository


@dataclass(frozen=True, slots=True)
class MatchedSourceRackDeparture:
    motion_id: int
    operation_id: str | None = None

    def __post_init__(self):
        if (
            type(self.motion_id) is not int
            or not 0 < self.motion_id <= 2**63 - 1
            or (
                self.operation_id is not None
                and (
                    type(self.operation_id) is not str or not self.operation_id.strip() or len(self.operation_id) > 120
                )
            )
        ):
            raise ValueError("source departure requires exact current identities")


class SourceRackDepartureFlow:
    def __init__(
        self,
        returns,
        creator,
        reader,
        *,
        scheduler=None,
        departure_reader=None,
        repository=None,
        clock=None,
        uuid_factory=None,
    ):
        self.returns, self.creator, self.reader = returns, creator, reader
        self.repository = repository or SourceRackDepartureRepository(returns.motions, departure_reader)
        self.clock = clock or timezone.now_for_db
        self.scheduler = scheduler
        self.uuid_factory = uuid_factory or new_uuid7

    async def advance_in_session(self, db, line):
        count = 0
        for current in await self.repository.pending(db, line.id):
            if current.departure_transport_task_id is None:
                count += await self._advance_decision(db, line, current)
            elif await self.repository.finished(db, current):
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
            if current.departure_operation_id is not None:
                if intent.operation_id is not None and current.departure_operation_id != intent.operation_id:
                    raise ValueError("departure frozen operation drift")
                continue
            if intent.operation_id is None:
                context = await self._context(db, line, current)
                if context is not None:
                    count += await self._request(db, line, current, context)
            else:
                count += await self._create_transport(db, line, current, intent.operation_id)
        return count

    async def _context(self, db, line, current):
        if current.closed_at is None or current.inbound_client_request_id is None or not line.is_active:
            return None
        context = await self.repository.current_context(db, line, current)
        if context is None or not await self.repository.service_finished(db, current):
            return None
        for allocation in await self.returns.repository.current(db, line.id):
            if (
                allocation.rack_id == current.rack_id
                and allocation.created_at >= current.round_started_at
                and not await self.returns._motion_obligation_closed(db, allocation)
            ):
                return None
        return context

    async def _request(self, db, line, current, context):
        if self.scheduler is None:
            return 0
        location, face = context
        operation_id = self.uuid_factory()
        intent = wms_operations.outbound_rack_departure_decide(
            operation_id=operation_id,
            task_id=None,
            rack_id=current.rack_id,
            current_location=location,
            current_face=face,
        )
        await self.scheduler.create_in_session(db, intent, workline_id=line.id, created_at=self.clock())
        current.departure_operation_id, current.departure_retry_at = operation_id, None
        await db.flush()
        return 1

    async def _advance_decision(self, db, line, current):
        snapshot = await self.repository.pending_decision(db, current)
        if snapshot is None:
            return 0
        context = await self._context(db, line, current)
        if context is None and await self.repository.superseded(db, current):
            current.archived_at = self.clock()
            await db.flush()
            return 1
        if snapshot.status != "COMPLETED":
            return 0
        if type(snapshot.outcome.result) is RackDepartureWait:
            changed = 0
            if current.departure_retry_at is None:
                changed = await self._remember_wait(db, current, snapshot)
            if self.clock() >= current.departure_retry_at and context is not None:
                return changed + await self._request(db, line, current, context)
            return changed
        return await self._create_transport(db, line, current, current.departure_operation_id)

    async def _remember_wait(self, db, current, snapshot):
        evidence = await self.returns.repository.evidence(db, snapshot.evidence_id)
        deadline = evidence.received_at + timedelta(milliseconds=snapshot.outcome.result.retry_after_ms)
        if current.departure_retry_at is not None:
            if current.departure_retry_at != deadline:
                raise ValueError("departure WAIT deadline differs from original response")
            return 0
        current.departure_retry_at = deadline
        await db.flush()
        return 1

    async def apply_decision_result_in_session(self, db, *, line, evidence):
        current = await self.repository.by_decision(db, line.id, evidence.operation_id)
        if current is None or current.archived_at is not None:
            return "SOURCE_DEPARTURE_OLD_DECISION"
        snapshot = await self.repository.decision(db, current, evidence.operation_id, evidence_id=evidence.id)
        if type(snapshot.outcome.result) is RackDepartureWait:
            await self._remember_wait(db, current, snapshot)
        return "SOURCE_DEPARTURE_DECIDED"

    async def _create_transport(self, db, line, current, operation_id):
        if await self._context(db, line, current) is None:
            return 0
        snapshot = await self.repository.ready(db, line, current, operation_id)
        if snapshot is None:
            return 0
        handle = await self.creator.create_source_return(
            db,
            workline_id=line.id,
            source_evidence_id=snapshot.evidence_id,
            correlation_id="departure:" + operation_id,
            step=DEPARTURE_STEP,
            rack_id=current.rack_id,
            destination=snapshot.outcome.result.rack_destination,
        )
        await self.repository.bind(db, current, operation_id, handle.transport_task_id)
        return 1

    async def apply_transport_result_in_session(self, db, *, line, evidence):
        return await self.repository.observe(db, line, evidence)
