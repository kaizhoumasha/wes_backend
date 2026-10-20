"""首次 READY 的离线事实读口；调用方持有 WorkLine 锁，不写业务进度。"""

from wes_plugin_sdk import BinReturnBatchReady, ReturnBufferDrainReady

from automatic_picking.drain_replenishment import DrainBufferFact, DrainReplenishmentFact
from src.app.wms_integration.return_buffer_drain import ReturnBufferDrainResultReader
from src.utils.timezone import timezone

from .drain_flow import MatchedDrainBuffer
from .drain_repository import DrainRepository
from .drain_return_repository import DrainReturnRepository
from .return_allocation_flow import MatchedReturnLocation


class FirstDrainReadyReader:
    def __init__(self, *, buffer_reader, location_reader):
        self.buffer_reader, self.location_reader = buffer_reader, location_reader
        self.repository = DrainRepository()
        self.reader = ReturnBufferDrainResultReader()

    async def _inflight(self, db, line, root):
        remaining = set(root.return_ids) - set(root.returned_ids)
        inflight = set()
        for allocation in await self.repository.allocations.current(db, line.id):
            selected = allocation.return_ids[: allocation.selected_count or 0]
            related = remaining.intersection(selected)
            if not related or allocation.handed_off_at is None:
                continue
            if allocation.drain_id not in (None, root.id) or related & inflight:
                raise ValueError("inflight execution differs from current drain scope")
            evidence = await self.repository.evidence(db, allocation.response_evidence_id)
            if evidence is None or evidence.apply_status != "APPLIED" or evidence.published_at is None:
                raise ValueError("inflight allocation requires its published original decision")
            intent, outcome = await self.repository.allocations.reader.read_return(db, evidence, workline_id=line.id)
            if (
                type(outcome.result) is not BinReturnBatchReady
                or intent.operation_id != allocation.operation_id
                or intent.workline_code != line.line_code
                or (intent.rack_id, intent.rack_face) != (allocation.rack_id, allocation.rack_face)
                or len(intent.return_candidates) != len(allocation.return_ids)
                or len(set(allocation.return_ids)) != len(allocation.return_ids)
                or len(outcome.result.moves) != allocation.selected_count
            ):
                raise ValueError("inflight allocation lost exact original decision identity")
            _, members = await self.repository.allocations.transport(db, allocation)
            for candidate, move, member in zip(
                intent.return_candidates[: allocation.selected_count], outcome.result.moves, members, strict=True
            ):
                if (
                    member.object_id != candidate.bin_code
                    or member.object_id != move.bin_code
                    or member.source_json
                    != {"kind": "HANDOFF_POSITION", "location_code": candidate.source_location_code}
                    or member.target_json
                    != {
                        "kind": "RACK_BIN_SLOT",
                        "rack_id": move.target.rack_id,
                        "rack_face": move.target.rack_face,
                        "slot_id": move.target.slot_id,
                    }
                ):
                    raise ValueError("inflight Transport member differs from frozen allocation")
            inflight.update(related)
        return tuple(i for i in root.return_ids if i in inflight)

    async def read_in_session(self, db, *, line, now):
        root = await self.repository.current(db, line.id)
        if root is None:
            return None
        DrainReturnRepository.validate_members(root)
        record = await self.reader.for_operation(db, workline_id=line.id, operation_id=root.operation_id)
        if record is None:
            raise ValueError("current drain lost its exact original request")
        if record.intent.workline_code != line.line_code or record.intent.required_slot_count != len(root.return_ids):
            raise ValueError("first READY request differs from current drain scope")
        if type(record.result) is not ReturnBufferDrainReady or root.response_evidence_id is None:
            return None  # 原 batch/FactProcessor 负责未知、未发布、未应用与首次 WAIT；不找历史 READY。
        if root.response_evidence_id != record.evidence_id or root.retry_at is not None:
            raise ValueError("first READY current Evidence identity drift")
        self.repository.validate_departure_handoffs(root, record.result)
        buffer = await self.buffer_reader.read_in_session(db, workline_id=line.id)
        if buffer is None:
            return None
        if type(buffer) is not MatchedDrainBuffer:
            raise ValueError("first READY requires exact buffer location/capacity")
        fifo, inflight = (), ()
        if set(root.departure_handoffs) == {rack.rack_id for rack in record.result.racks}:
            rows = await self.repository.returns.list_fifo(db, line.id)
            if rows is None:
                return None
            inflight = await self._inflight(db, line, root)
            facts = []
            for row in rows:
                position = await self.location_reader.read_in_session(db, return_id=row.id)
                if position is not None and (
                    type(position) is not MatchedReturnLocation
                    or (position.return_id, position.workline_id, position.bin_code) != (row.id, line.id, row.bin_code)
                ):
                    raise ValueError("current buffer location differs from exact Return identity")
                facts.append(
                    DrainBufferFact(
                        row.id,
                        line.id,
                        position.location_code if position else None,
                        row.allocation_id is not None,
                        bool(await self.repository.allocations.qualified(db, row)),
                    )
                )
            fifo = tuple(facts)
        return DrainReplenishmentFact(
            workline_id=line.id,
            operation_id=root.operation_id,
            reservation_operation_id=root.operation_id,
            return_ids=tuple(root.return_ids),
            returned_ids=tuple(root.returned_ids),
            inflight_return_ids=inflight,
            fifo=fifo,
            buffer_location_code=buffer.location_code,
            outcome=record.result,
            outcome_operation_id=record.intent.operation_id,
            published=True,
            departure_handoffs=tuple(root.departure_handoffs.items()),
            retry_at=None,
            now=int(timezone.to_utc(now).timestamp() * 1000),
        )
