"""普通来源架换面的离线事务入口；完整参数由获批读口给定。"""

from dataclasses import dataclass

from wes_plugin_sdk import TransportRackPosition, wms_operations

from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone

from .feed_repository import FeedRepository
from .source_rack_motion_repository import ROTATION_STEP, SourceRackMotionRepository


@dataclass(frozen=True, slots=True)
class MatchedSourceRackRotation:
    motion_id: int
    next_member_id: int
    rack_id: str
    position: TransportRackPosition
    target_face: str

    def __post_init__(self):
        if any(type(v) is not int or not 0 < v <= 2**63 - 1 for v in (self.motion_id, self.next_member_id)):
            raise ValueError("source rotation requires exact current identities")
        if type(self.position) is not TransportRackPosition or any(
            type(v) is not str or not v.strip() for v in (self.rack_id, self.target_face)
        ):
            raise ValueError("source rotation requires complete explicit parameters")


class SourceRackMotionFlow:
    def __init__(self, returns, creator, scheduler, reader, *, repository=None, clock=None, uuid_factory=None):
        self.returns, self.creator, self.scheduler, self.reader = returns, creator, scheduler, reader
        self.repository = repository or SourceRackMotionRepository(returns.motions)
        self.feeds = FeedRepository()
        self.clock, self.uuid_factory = clock or timezone.now_for_db, uuid_factory or new_uuid7

    async def advance_in_session(self, db, line):
        count = 0
        for current in await self.repository.pending(db, line.id):
            count += await self._handoff(db, line, current)
        intents = await self.reader.read_in_session(db, workline_id=line.id) if self.reader else None
        if intents is not None and type(intents) is not tuple:
            raise TypeError("rotation requires immutable explicit inputs")
        for intent in intents or ():
            if type(intent) is not MatchedSourceRackRotation:
                raise TypeError("rotation requires typed explicit parameters")
            current = await self.repository.by_id(db, intent.motion_id)
            if current is None or current.workline_id != line.id:
                raise ValueError("rotation lost exact motion stage")
            if current.archived_at is not None or current.departure_operation_id is not None:
                continue
            expected = {"kind": "RACK_POSITION", "location_code": intent.position.location_code}
            if (
                intent.rack_id != current.rack_id
                or intent.target_face == current.rack_face
                or intent.position.location_code != line.position_bindings["FIVE_RACK"]["location_id"]
            ):
                raise ValueError("rotation parameters differ from current source position")
            if current.rotation_transport_task_id is not None:
                task, _binding = await self.repository.transport(db, current)
                if (
                    task.request_json.get("position") != expected
                    or task.request_json.get("target_face") != intent.target_face
                ):
                    raise ValueError("rotation frozen parameters drift")
                continue
            if (
                current.archived_at is not None
                or current.closed_at is None
                or not line.is_active
                or current.inbound_client_request_id is None
            ):
                continue
            if not await self.repository.next_member(db, line, current, intent):
                continue
            if not await self.returns.motions.current_source(db, line, current):
                continue
            allocations = await self.returns.repository.current(db, line.id)
            blocked = False
            for allocation in allocations:
                if (
                    allocation.rack_id == current.rack_id
                    and allocation.created_at >= current.round_started_at
                    and not await self.returns._motion_obligation_closed(db, allocation)
                ):
                    blocked = True
                    break
            if blocked:
                continue
            end = await self.returns.repository.evidence(db, current.service_end_evidence_id)
            handle = await self.creator.create_rotate(
                db,
                workline_id=line.id,
                picking_task_id=end.picking_task_id,
                source_evidence_id=current.service_end_evidence_id,
                correlation_id=f"auto-source-rotation:{current.id}",
                step=ROTATION_STEP,
                rack_id=current.rack_id,
                position=intent.position,
                target_face=intent.target_face,
            )
            await self.repository.bind(db, current, handle.transport_task_id)
            count += 1
        return count

    async def apply_transport_result_in_session(self, db, *, line, evidence):
        return await self.repository.observe(db, line, evidence)

    async def _handoff(self, db, line, current):
        task, binding = await self.repository.transport(db, current)
        evidence = (
            await self.returns.repository.evidence(db, current.rotation_result_evidence_id)
            if current.rotation_result_evidence_id
            else None
        )
        if (
            evidence is None
            or evidence.published_at is None
            or task.outcome_version < 1
            or task.outcome_version != task.published_outcome_version
        ):
            return 0
        if evidence.normalized_payload.get("outcome_version") != task.outcome_version:
            return 0
        face = task.request_json["target_face"]
        if task.status != "SUCCEEDED" or not await self.returns.motions.current_source(
            db, line, current, arrival_evidence_id=evidence.id, rack_face=face
        ):
            if await self.repository.superseded(db, current, binding):
                current.archived_at = self.clock()
                await db.flush()
                return 1
            return 0
        if not line.is_active:
            return 0
        count = 0
        owner, members = await self.repository.service_members(db, line, current, face)
        handed_off = False
        if owner is not None and owner.status in {"EXECUTION_COMPLETED", "CANCELLED", "ARCHIVED"}:
            # 最后来源轮次仍由离场扫描承接，父任务终态不证明已离场。
            return 0
        for member in members:
            feed = await self.feeds.by_member(db, member.id)
            if feed is not None:
                if (
                    feed.inbound_client_request_id,
                    feed.round_started_at,
                    feed.round_causal_token,
                    feed.source_arrival_evidence_id,
                ) != (
                    current.inbound_client_request_id,
                    current.round_started_at,
                    current.round_causal_token,
                    evidence.id,
                ):
                    raise ValueError("next Feed differs from rotation handoff")
                handed_off = True
                continue
            now, operation_id = self.clock(), self.uuid_factory()
            await self.feeds.add_feed(
                db,
                workline_id=line.id,
                picking_task_id=member.picking_task_id,
                source_member_id=member.id,
                operation_id=operation_id,
                inbound_client_request_id=current.inbound_client_request_id,
                round_started_at=current.round_started_at,
                round_causal_token=current.round_causal_token,
                source_arrival_evidence_id=evidence.id,
            )
            await self.scheduler.create_in_session(
                db,
                wms_operations.outbound_bin_inbound_batch(
                    operation_id=operation_id,
                    task_id=owner.task_id,
                    plan_revision=member.plan_revision,
                    rack_id=member.rack_id,
                    rack_face=member.rack_face,
                ),
                workline_id=line.id,
                created_at=now,
            )
            count += 1
            handed_off = True
        if handed_off:
            current.archived_at = self.clock()
            await db.flush()
        return count
