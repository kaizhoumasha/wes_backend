"""自动回架的本地事务编排；精确位置与当前架面由获批读口提供。"""

from dataclasses import dataclass

from wes_plugin_sdk import BinBatchNoBatch, BinReturnBatchReady, BinReturnCandidate, wms_operations

from src.app.transport.contracts import BinMove, HandoffPosition, RackBinSlot
from src.core.uuid7 import new_uuid7
from src.utils.canonical_json import canonical_json_digest
from src.utils.timezone import timezone


@dataclass(frozen=True, slots=True)
class MatchedReturnFace:
    arrival_evidence_id: int
    workline_id: int
    rack_id: str
    rack_face: str

    def __post_init__(self):
        if any(type(v) is not int or not 0 < v <= 2**63 - 1 for v in (self.arrival_evidence_id, self.workline_id)):
            raise ValueError("current return face requires exact identity")
        if any(type(v) is not str or not v.strip() for v in (self.rack_id, self.rack_face)):
            raise ValueError("current return face is required")


@dataclass(frozen=True, slots=True)
class MatchedReturnLocation:
    return_id: int
    workline_id: int
    bin_code: str
    location_code: str

    def __post_init__(self):
        if any(type(v) is not int or not 0 < v <= 2**63 - 1 for v in (self.return_id, self.workline_id)):
            raise ValueError("return location requires exact identity")
        BinReturnCandidate(1, self.bin_code, self.location_code)


class AutomaticPickingReturnBatchFlow:
    def __init__(
        self,
        repository,
        scheduler,
        creator,
        face_reader,
        location_reader,
        *,
        clock=timezone.now_for_db,
        uuid_factory=new_uuid7,
    ):
        self.repository, self.scheduler, self.creator = repository, scheduler, creator
        self.face_reader, self.location_reader = face_reader, location_reader
        self.clock, self.uuid_factory = clock, uuid_factory

    async def _prefix(self, db, line):
        fifo = await self.repository.returns.list_fifo(db, line.id)
        if fifo is None:
            return (), ()
        rows, candidates = [], []
        for row in fifo:
            if row.allocation_id is not None:
                continue
            if not await self.repository.qualified(db, row):
                break
            location = await self.location_reader.read_in_session(db, return_id=row.id)
            if location is None:
                break
            if (
                type(location) is not MatchedReturnLocation
                or location.return_id != row.id
                or location.workline_id != line.id
                or location.bin_code != row.bin_code
            ):
                raise ValueError("current return position differs from execution")
            rows.append(row)
            candidates.append(BinReturnCandidate(len(rows), row.bin_code, location.location_code))
            if len(rows) == 4:
                break
        return rows, tuple(candidates)

    async def _faces(self, db, line):
        faces = await self.face_reader.read_in_session(db, workline_id=line.id)
        if faces is None:
            return (), False
        if type(faces) is not tuple:
            raise TypeError("current return faces require immutable explicit facts")
        known, complete = [], True
        for face in faces:
            if type(face) is not MatchedReturnFace or face.workline_id != line.id:
                raise ValueError("current return face differs from WorkLine")
            evidence = await self.repository.evidence(db, face.arrival_evidence_id)
            if (
                evidence is None
                or evidence.workline_id != line.id
                or evidence.apply_status != "APPLIED"
                or evidence.kind not in {"DEVICE_EVENT", "TRANSPORT_RESULT"}
                or evidence.published_at is None
                or canonical_json_digest(evidence.normalized_payload) != evidence.payload_digest
            ):
                complete = False
                continue
            known.append(face)
        return tuple(known), complete

    async def advance_in_session(self, db, line):
        allocations = await self.repository.current(db, line.id)
        faces, complete = await self._faces(db, line)
        count = 0
        for allocation in allocations:
            count += await self._advance_allocation(db, line, allocation, faces or ())
        if complete:
            await self.repository.retire_completed(db, allocations, faces, now=self.clock())
        if not line.is_active:
            return count
        for face in faces:
            same_arrival = [
                a
                for a in allocations
                if a.arrival_evidence_id == face.arrival_evidence_id
                and a.rack_id == face.rack_id
                and a.rack_face == face.rack_face
            ]
            if any(a.selected_count == 0 for a in same_arrival):
                continue
            if any(
                a.rack_id == face.rack_id and a.rack_face == face.rack_face and a.settled_at is None
                for a in allocations
            ):
                continue
            rows, candidates = await self._prefix(db, line)
            if not candidates:
                continue
            operation_id, now = self.uuid_factory(), self.clock()
            await self.repository.add(
                db,
                line=line,
                face=face,
                operation_id=operation_id,
                rows=rows,
                completed=tuple(a for a in same_arrival if a.settled_at is not None and a.handed_off_at is not None),
                now=now,
            )
            intent = wms_operations.outbound_bin_return_batch(
                operation_id=operation_id,
                workline_code=line.line_code,
                rack_id=face.rack_id,
                rack_face=face.rack_face,
                return_candidates=candidates,
            )
            await self.scheduler.create_in_session(db, intent, workline_id=line.id, created_at=now)
            allocations = await self.repository.current(db, line.id)
            count += 1
        return count

    async def apply_result_in_session(self, db, *, line, evidence):
        allocation = await self.repository.by_operation(db, evidence.operation_id)
        if (
            allocation is None
            or allocation.workline_id != line.id
            or evidence.workline_id != line.id
            or evidence.operation != "outbound.bin.return_batch@v1"
            or canonical_json_digest(evidence.normalized_payload) != evidence.payload_digest
        ):
            return "RECONCILING"
        if allocation.response_evidence_id is not None:
            return "RETURN_DECIDED" if allocation.response_evidence_id == evidence.id else "RECONCILING"
        try:
            intent, outcome = await self.repository.reader.read_return(db, evidence, workline_id=line.id)
            if (
                intent.operation_id != allocation.operation_id
                or intent.workline_code != line.line_code
                or intent.rack_id != allocation.rack_id
                or intent.rack_face != allocation.rack_face
                or len(intent.return_candidates) != len(allocation.return_ids)
            ):
                return "RECONCILING"
            for return_id, candidate in zip(allocation.return_ids, intent.return_candidates, strict=True):
                current = await self.repository.returns.by_id_for_update(db, return_id)
                if current is None or current.bin_code != candidate.bin_code:
                    return "RECONCILING"
            result = outcome.result
            if type(result) not in {BinReturnBatchReady, BinBatchNoBatch}:
                return "RECONCILING"
            selected_count = len(result.moves) if type(result) is BinReturnBatchReady else 0
            await self.repository.apply_decision(db, allocation, evidence.id, selected_count, now=self.clock())
        except (TypeError, ValueError):
            return "RECONCILING"
        return "RETURN_DECIDED"

    async def _create_transport(self, db, line, allocation, faces):
        if not line.is_active or not any(
            f.arrival_evidence_id == allocation.arrival_evidence_id
            and f.workline_id == line.id
            and f.rack_id == allocation.rack_id
            and f.rack_face == allocation.rack_face
            for f in faces
        ):
            return 0
        evidence = await self.repository.evidence(db, allocation.response_evidence_id)
        if evidence is None or evidence.published_at is None:
            return 0
        intent, outcome = await self.repository.reader.read_return(db, evidence, workline_id=line.id)
        if type(outcome.result) is not BinReturnBatchReady or len(outcome.result.moves) != allocation.selected_count:
            raise ValueError("current return READY changed its frozen allocation")
        moves = []
        for index, move in enumerate(outcome.result.moves):
            return_id = allocation.return_ids[index]
            location = await self.location_reader.read_in_session(db, return_id=return_id)
            candidate = intent.return_candidates[index]
            if location is None:
                return 0
            if (
                type(location) is not MatchedReturnLocation
                or location.return_id != return_id
                or location.workline_id != line.id
                or location.bin_code != candidate.bin_code
                or location.location_code != candidate.source_location_code
            ):
                raise ValueError("return position differs from frozen request source")
            moves.append(
                BinMove(
                    move.bin_code,
                    HandoffPosition(candidate.source_location_code),
                    RackBinSlot(move.target.rack_id, move.target.rack_face, move.target.slot_id),
                )
            )
        if await self.repository.target_conflict(db, tuple(moves)):
            return 0
        handle = await self.creator.create(
            db,
            workline_id=line.id,
            source_evidence_id=evidence.id,
            correlation_id=allocation.operation_id,
            step="AUTOMATIC_PICKING_RETURN",
            resource_fence_id=allocation.operation_id,
            moves=tuple(moves),
        )
        await self.repository.bind_transport(db, allocation, handle.transport_task_id)
        return 1

    async def _advance_allocation(self, db, line, allocation, faces):
        if allocation.settled_at is not None or allocation.response_evidence_id is None:
            return 0
        if allocation.transport_task_id is None:
            return await self._create_transport(db, line, allocation, faces)
        task, members = await self.repository.transport(db, allocation)
        count = 0
        if allocation.handed_off_at is None and task.status in {"ACCEPTED", "SUCCEEDED"}:
            await self.repository.handoff(db, allocation, members, now=self.clock())
            count += 1
        evidence = (
            await self.repository.evidence(db, allocation.transport_result_evidence_id)
            if allocation.transport_result_evidence_id
            else None
        )
        if (
            evidence is None
            or evidence.published_at is None
            or task.outcome_version < 1
            or task.outcome_version != task.published_outcome_version
            or evidence.normalized_payload["outcome_version"] != task.outcome_version
        ):
            return count
        if task.status == "REJECTED" or (
            task.status == "SUCCEEDED"
            and all(
                m.status == "SUCCEEDED" and not m.position_unknown and m.final_position_json == m.target_json
                for m in members
            )
        ):
            await self.repository.mark_settled(db, allocation, now=self.clock())
            count += 1
        return count

    async def apply_transport_result_in_session(self, db, *, line, evidence):
        allocation = await self.repository.by_transport(db, evidence.transport_task_id)
        if allocation is None:
            return "IGNORED"
        task, _members = await self.repository.transport(db, allocation)
        payload = evidence.normalized_payload
        if (
            evidence.workline_id != line.id
            or allocation.workline_id != line.id
            or payload.get("transport_task_id") != task.transport_task_id
            or payload.get("client_request_id") != task.client_request_id
            or payload.get("step") != "AUTOMATIC_PICKING_RETURN"
            or payload.get("source_evidence_id") != allocation.response_evidence_id
            or canonical_json_digest(payload) != evidence.payload_digest
        ):
            return "RECONCILING"
        version = payload.get("outcome_version")
        if type(version) is not int or version > task.outcome_version:
            return "RECONCILING"
        if version < task.outcome_version or allocation.archived_at is not None:
            return "RETURN_OLD_OUTCOME"
        allocation.transport_result_evidence_id = evidence.id
        await db.flush()
        return "RETURN_TRANSPORT_OBSERVED"
