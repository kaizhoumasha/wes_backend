"""自动回架的本地事务编排；精确位置与当前架面由获批读口提供。"""

from dataclasses import dataclass

from wes_plugin_sdk import BinBatchNoBatch, BinReturnBatchReady, BinReturnCandidate, wms_operations

from src.app.transport.contracts import BinMove, HandoffPosition, RackBinSlot
from src.core.uuid7 import new_uuid7
from src.utils.canonical_json import canonical_json_digest
from src.utils.timezone import timezone

from .drain_return_repository import DrainReturnRepository
from .return_motion_repository import ReturnMotionRepository


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


@dataclass(frozen=True, slots=True)
class MatchedReturnMotion:
    face: MatchedReturnFace
    source_evidence_id: int
    inbound_client_request_id: str
    source_feed_id: int
    service_end_evidence_id: int

    def __post_init__(self):
        if (
            type(self.face) is not MatchedReturnFace
            or type(self.source_evidence_id) is not int
            or any(
                type(v) is not int or not 0 < v <= 2**63 - 1
                for v in (self.source_evidence_id, self.source_feed_id, self.service_end_evidence_id)
            )
            or type(self.inbound_client_request_id) is not str
            or not 0 < len(self.inbound_client_request_id.strip()) <= 120
        ):
            raise ValueError("motion evaluation requires exact face and business Evidence")


class AutomaticPickingReturnBatchFlow:
    def __init__(
        self,
        repository,
        scheduler,
        creator,
        face_reader,
        location_reader,
        *,
        motion_reader=None,
        motions=None,
        clock=timezone.now_for_db,
        uuid_factory=new_uuid7,
    ):
        self.repository, self.scheduler, self.creator = repository, scheduler, creator
        self.drains = DrainReturnRepository()
        self.face_reader, self.location_reader = face_reader, location_reader
        self.clock, self.uuid_factory = clock, uuid_factory
        self.motion_reader, self.motions = motion_reader, motions or ReturnMotionRepository()

    async def _prefix(self, db, line, *, drain=None, drain_face=False):
        fifo = await self.repository.returns.list_fifo(db, line.id)
        if fifo is None:
            return None
        rows, candidates = [], []
        for row in fifo:
            if not self.drains.allowed(drain, row, drain_face=drain_face):
                if drain_face and drain is not None and row.id not in drain[0].return_ids:
                    return rows, tuple(candidates)
                return (rows, tuple(candidates)) if rows else None
            if row.allocation_id is not None:
                if drain_face or (drain is not None and row.id in drain[0].return_ids):
                    return (rows, tuple(candidates)) if rows else None
                continue
            if not await self.repository.qualified(db, row):
                return (rows, tuple(candidates)) if rows else None
            location = await self.location_reader.read_in_session(db, return_id=row.id)
            if location is None:
                return (rows, tuple(candidates)) if rows else None
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

    @staticmethod
    def _face_identity(value):
        return value.arrival_evidence_id, value.rack_id, value.rack_face

    async def _motion_obligation_closed(self, db, allocation):
        evidence = (
            await self.repository.evidence(db, allocation.response_evidence_id)
            if allocation.response_evidence_id
            else None
        )
        if evidence is None or evidence.apply_status != "APPLIED" or evidence.published_at is None:
            return False
        if allocation.selected_count == 0:
            return True
        if allocation.settled_at is None:
            return False
        task, _members = await self.repository.transport(db, allocation)
        if task.status != "REJECTED" and allocation.handed_off_at is None:
            return False
        result = (
            await self.repository.evidence(db, allocation.transport_result_evidence_id)
            if allocation.transport_result_evidence_id
            else None
        )
        return result is not None and result.apply_status == "APPLIED" and result.published_at is not None

    @staticmethod
    def _motion_allocation_matches(allocation, motion, drain):
        if allocation.rack_id != motion.rack_id:
            return False
        if drain is not None and motion.source_evidence_id == drain[0].response_evidence_id:
            return allocation.drain_id == drain[0].id or allocation.arrival_evidence_id == motion.arrival_evidence_id
        return motion.round_started_at is not None and allocation.created_at >= motion.round_started_at

    async def _motion_can_close(self, db, allocation, allocations, motion, drain=None):
        if not await self._motion_obligation_closed(db, allocation):
            return False
        for original in allocations:
            if (
                self._motion_allocation_matches(original, motion, drain)
                and original.id != allocation.id
                and not await self._motion_obligation_closed(db, original)
            ):
                return False
        return True

    async def _motion_phases(self, db, line, faces, complete, allocations, drain=None):
        phases, count = await self.motions.current(db, line.id), 0
        intents = await self.motion_reader.read_in_session(db, workline_id=line.id) if self.motion_reader else ()
        if intents is not None and type(intents) is not tuple:
            raise TypeError("motion intents require immutable explicit input")
        for intent in intents or ():
            if type(intent) is not MatchedReturnMotion or intent.face.workline_id != line.id:
                raise ValueError("motion intent differs from WorkLine")
            if not line.is_active or intent.face not in faces:
                continue
            evidence = await self.repository.evidence(db, intent.source_evidence_id)
            if (
                evidence is None
                or evidence.workline_id != line.id
                or evidence.apply_status != "APPLIED"
                or evidence.published_at is None
                or canonical_json_digest(evidence.normalized_payload) != evidence.payload_digest
            ):
                raise ValueError("motion intent requires published authoritative business Evidence")
            current = next((p for p in phases if self._face_identity(p) == self._face_identity(intent.face)), None)
            if current is not None and current.source_evidence_id != intent.source_evidence_id:
                raise ValueError("motion trigger identity drift")
            if current is not None and current.inbound_client_request_id is not None:
                if (current.inbound_client_request_id, current.source_feed_id, current.service_end_evidence_id) != (
                    intent.inbound_client_request_id,
                    intent.source_feed_id,
                    intent.service_end_evidence_id,
                ):
                    raise ValueError("motion source binding identity drift")
                continue
            root = await self.motions.source_binding(db, line, intent)
            if root is None:
                continue
            if current is None:
                current = await self.motions.add(db, intent)
                phases.append(current)
            await self.motions.bind_source(db, current, intent, root)
            count += 1
        for current in phases:
            if (
                current.closed_at is None
                and current.allocation_id is not None
                and (
                    current.inbound_client_request_id is not None
                    or (drain is not None and current.source_evidence_id == drain[0].response_evidence_id)
                )
            ):
                allocation = await self.motions.allocation(db, current)
                if await self._motion_can_close(db, allocation, allocations, current, drain):
                    await self.motions.close(db, current, now=self.clock())
                    count += 1
        if complete:
            await self.motions.retire(db, phases, faces, line=line, now=self.clock())
        return phases, count

    async def advance_in_session(self, db, line):
        allocations = await self.repository.current(db, line.id)
        drain = await self.drains.current(db, line)
        faces, complete = await self._faces(db, line)
        faces, drain_faces = await self.drains.faces(db, line, drain, faces)
        count = 0
        for allocation in allocations:
            count += await self._advance_allocation(db, line, allocation, faces or ())
        phases, motion_count = await self._motion_phases(db, line, faces, complete, allocations, drain)
        count += motion_count
        if complete:
            await self.repository.retire_completed(db, allocations, faces, now=self.clock())
        if not line.is_active:
            return count
        for face in faces:
            motion = next(
                (p for p in phases if p.archived_at is None and self._face_identity(p) == self._face_identity(face)),
                None,
            )
            if (
                motion is None
                and self.motion_reader is not None
                and await self.motions.current_arrival(
                    db,
                    line.id,
                    face.arrival_evidence_id,
                    face.rack_id,
                    face.rack_face,
                    {"kind": "RACK_POSITION", "location_code": line.position_bindings["FIVE_RACK"]["location_id"]},
                )
                is None
            ):
                continue
            if motion is not None and (motion.closed_at is not None or motion.allocation_id is not None):
                continue
            if (
                motion is not None
                and face not in drain_faces
                and not await self.motions.current_source(db, line, motion)
            ):
                continue
            same_arrival = [
                a
                for a in allocations
                if a.arrival_evidence_id == face.arrival_evidence_id
                and a.rack_id == face.rack_id
                and a.rack_face == face.rack_face
            ]
            no_batch = next((a for a in same_arrival if a.selected_count == 0), None)
            if no_batch is not None:
                if motion is not None:
                    await self.motions.bind(db, motion, no_batch)
                    count += 1
                    if await self._motion_can_close(db, no_batch, allocations, motion, drain):
                        await self.motions.close(db, motion, now=self.clock())
                        count += 1
                continue
            if motion is not None:
                blocked = False
                for original in allocations:
                    if self._motion_allocation_matches(
                        original, motion, drain
                    ) and not await self._motion_obligation_closed(db, original):
                        blocked = True
                        break
                if blocked:
                    continue
            if any(
                a.rack_id == face.rack_id
                and a.rack_face == face.rack_face
                and a.settled_at is None
                and (motion is None or a.created_at >= motion.round_started_at)
                for a in allocations
            ):
                continue
            prefix = await self._prefix(db, line, drain=drain, drain_face=face in drain_faces)
            if prefix is None:
                continue
            rows, candidates = prefix
            if not candidates:
                if motion is not None:
                    await self.motions.close(db, motion, now=self.clock())
                    count += 1
                continue
            operation_id, now = self.uuid_factory(), self.clock()
            allocation = await self.repository.add(
                db,
                line=line,
                face=face,
                operation_id=operation_id,
                rows=rows,
                drain_id=self.drains.owner(drain, rows),
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
            if motion is not None:
                await self.motions.bind(db, motion, allocation)
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
        count += await self.drains.record_returned(db, allocation, task, members, evidence, self.repository.reader)
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
