"""下一任务优先的排空决定与 WAIT 恢复；READY 可显式接入离线进场 owner。"""

from dataclasses import dataclass
from datetime import timedelta

from wes_plugin_sdk import ReturnBufferDrainReady, ReturnBufferDrainWait, wms_operations

from automatic_picking.drain_replenishment import evaluate_replenishment
from src.app.wms_integration.outbound_picking.services.picking_task_prepare import PickingTaskPrepareNoopReason
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone

from .drain_return_repository import DrainReturnRepository
from .return_allocation_flow import MatchedReturnLocation


@dataclass(frozen=True, slots=True)
class MatchedDrainBuffer:
    location_code: str
    capacity: int

    def __post_init__(self):
        if type(self.location_code) is not str or not self.location_code.strip():
            raise ValueError("drain requires an explicit authoritative buffer location")
        if type(self.capacity) is not int or self.capacity < 1:
            raise ValueError("drain requires explicit positive buffer capacity")


class AutomaticPickingDrainFlow:
    def __init__(
        self,
        repository,
        prepare,
        scheduler,
        reader,
        buffer_reader,
        location_reader,
        *,
        inbound=None,
        replenishment_reader=None,
        clock=timezone.now_for_db,
        uuid_factory=new_uuid7,
    ):
        self.repository, self.prepare, self.scheduler, self.reader = repository, prepare, scheduler, reader
        self.buffer_reader, self.location_reader = buffer_reader, location_reader
        self.clock, self.uuid_factory = clock, uuid_factory
        self.inbound = inbound
        self.replenishment_reader = replenishment_reader

    async def _candidates(self, db, line):
        buffer = await self.buffer_reader.read_in_session(db, workline_id=line.id)
        if buffer is None:
            return None
        if type(buffer) is not MatchedDrainBuffer:
            raise TypeError("drain requires immutable authoritative buffer input")
        rows = await self.repository.returns.list_fifo(db, line.id)
        if rows is None:
            return None
        result = []
        for row in rows:
            if row.allocation_id is not None or not await self.repository.allocations.qualified(db, row):
                return result or None
            position = await self.location_reader.read_in_session(db, return_id=row.id)
            if position is None:
                return result or None
            if type(position) is not MatchedReturnLocation or (
                position.return_id,
                position.workline_id,
                position.bin_code,
            ) != (row.id, line.id, row.bin_code):
                raise ValueError("drain buffer position differs from current Return identity")
            if position.location_code != buffer.location_code:
                break
            result.append(row.id)
            if len(result) > buffer.capacity:
                raise ValueError("confirmed drain members exceed authoritative buffer capacity")
        return result

    async def _remember(self, db, current, evidence, intent, outcome):
        DrainReturnRepository.validate_members(current)
        if intent.operation_id != current.operation_id or intent.required_slot_count != len(current.request_return_ids):
            raise ValueError("drain result differs from frozen request members")
        if type(outcome.result) is ReturnBufferDrainWait:
            due = evidence.received_at + timedelta(milliseconds=outcome.result.retry_after_ms)
        elif type(outcome.result) is ReturnBufferDrainReady:
            due = None
        else:
            raise ValueError("unknown typed drain result")
        count = 0
        if type(outcome.result) is ReturnBufferDrainReady and current.started_operation_id is None:
            current.started_operation_id = current.operation_id
            count = 1
        if current.response_evidence_id is not None:
            if (current.response_evidence_id, current.retry_at) != (evidence.id, due):
                raise ValueError("drain result or WAIT deadline drift")
            await db.flush()
            return count
        current.response_evidence_id, current.retry_at = evidence.id, due
        await db.flush()
        return 1

    async def apply_result_in_session(self, db, *, line, evidence):
        current = await self.repository.current(db, line.id)
        if current is None or current.operation_id != evidence.operation_id:
            return "IGNORED"
        intent, outcome = await self.reader.read(db, evidence, workline_id=line.id)
        await self._remember(db, current, evidence, intent, outcome)
        return "DRAIN_DECIDED"

    async def prepare_return_motion_in_session(self, db, line):
        if self.inbound is None or self.inbound.rotation is None:
            return 0
        count = await self.inbound.departure.recover_in_session(db, line) if self.inbound.departure is not None else 0
        return count + await self.inbound.rotation.prepare_in_session(
            db, line, include_final=self.inbound.departure is not None
        )

    async def advance_in_session(self, db, line):
        current = await self.repository.current(db, line.id)
        count = 0
        if current is not None:
            snapshot = await self.reader.for_operation(db, workline_id=line.id, operation_id=current.operation_id)
            if snapshot is None:
                raise ValueError("current drain request is missing")
            if snapshot.result is None:
                return 0
            evidence = await self.repository.evidence(db, snapshot.evidence_id)
            # read() validates the same reliable identity for live application and scan recovery.
            intent, outcome = await self.reader.read(db, evidence, workline_id=line.id)
            count += await self._remember(db, current, evidence, intent, outcome)
            if type(snapshot.result) is ReturnBufferDrainReady:
                DrainReturnRepository.validate_members(current)
                self.repository.validate_departure_handoffs(current, snapshot.result)
                if set(current.returned_ids) == set(current.return_ids) and set(current.departure_handoffs) == {
                    rack.rack_id for rack in snapshot.result.racks
                }:
                    current.archived_at = self.clock()
                    await db.flush()
                    return count + 1
                progressed = (
                    await self.inbound.advance_in_session(db, line, current, snapshot.result)
                    if self.inbound is not None
                    else 0
                )
                return count + progressed + (0 if progressed else await self._continue(db, line, current))
            if current.started_operation_id is not None:
                return count + await self._continue(db, line, current)
            if self.clock() < current.retry_at:
                return count
        if not line.is_active:
            return count
        return count + await self._admit(db, line, current)

    async def _continue(self, db, line, current):
        if self.replenishment_reader is None:
            return 0
        fact = await self.replenishment_reader.read_in_session(db, line=line, now=self.clock())
        if fact is None:
            return 0
        result = evaluate_replenishment(fact)
        if result.action == "EXIT_WAIT":
            current.archived_at = self.clock()
            await db.flush()
            return 1
        if result.action != "REQUEST" or not line.is_active:
            return 0
        operation_id, now = self.uuid_factory(), self.clock()
        intent = wms_operations.workline_return_buffer_drain_rack_decide(
            operation_id=operation_id,
            workline_code=line.line_code,
            required_slot_count=len(result.request_return_ids),
        )
        await self.scheduler.create_in_session(db, intent, workline_id=line.id, created_at=now)
        await self.repository.continue_current(
            db,
            current=current,
            operation_id=operation_id,
            remaining=result.remaining_return_ids,
            requested=result.request_return_ids,
            now=now,
        )
        return 1

    async def _admit(self, db, line, current):
        prepared = await self.prepare.prepare_next_in_session(db, line, now=self.clock())
        if prepared.prepared:
            await self._retire_wait(db, current)
            return 1
        if prepared.reason != PickingTaskPrepareNoopReason.NO_ELIGIBLE_TASK:
            if await self.repository.tasks.has_active_for_workline(db, line.id):
                return await self._retire_wait(db, current)
            return 0
        if await self.repository.normal_service_pending(db, line.id):
            return await self._retire_wait(db, current)
        members = await self._candidates(db, line)
        if members is None:
            return 0
        if not members:
            return await self._retire_wait(db, current)
        operation_id, now = self.uuid_factory(), self.clock()
        intent = wms_operations.workline_return_buffer_drain_rack_decide(
            operation_id=operation_id, workline_code=line.line_code, required_slot_count=len(members)
        )
        await self.scheduler.create_in_session(db, intent, workline_id=line.id, created_at=now)
        await self.repository.replace(
            db, current=current, line_id=line.id, operation_id=operation_id, return_ids=members
        )
        return 1

    async def _retire_wait(self, db, current):
        if current is None:
            return 0
        current.archived_at = self.clock()
        await db.flush()
        return 1
