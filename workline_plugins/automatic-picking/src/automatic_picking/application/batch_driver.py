"""现有计划周期入口的自动业务 owner，不依赖一次性通知。"""

from wes_plugin_sdk import BinInboundBatchRackFaceDone, BinInboundBatchReady, wms_operations

from src.app.wms_integration.outbound_picking.repositories.picking_task_repository import PickingTaskRepository
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone


class AutomaticPickingBatchDriver:
    def __init__(
        self,
        works,
        feeds,
        work_flow,
        feed_flow,
        batch_scheduler,
        *,
        tasks=None,
        source_flow=None,
        direct_pick_flow=None,
        material_flow=None,
        target_flow=None,
        exit_flow=None,
        return_batch_flow=None,
        rack_motion_flow=None,
        rack_departure_flow=None,
        drain_flow=None,
        return_rack_flow=None,
        uuid_factory=new_uuid7,
        clock=timezone.now_for_db,
    ):
        self.works, self.feeds = works, feeds
        self.work_flow, self.feed_flow = work_flow, feed_flow
        self.batch_scheduler = batch_scheduler
        self.tasks = tasks or PickingTaskRepository()
        self.source_flow = source_flow
        self.direct_pick_flow = direct_pick_flow
        self.material_flow = material_flow
        self.target_flow = target_flow
        self.exit_flow = exit_flow
        self.return_batch_flow = return_batch_flow
        self.rack_motion_flow = rack_motion_flow
        self.rack_departure_flow = rack_departure_flow
        self.drain_flow = drain_flow
        self.return_rack_flow = return_rack_flow
        self.final_target_flow = None
        self.completion_flow = None
        self.uuid_factory, self.clock = uuid_factory, clock

    async def filter_return_racks_in_session(self, db, _line, task, candidates):
        if self.return_rack_flow is None:
            return candidates
        from dataclasses import replace

        from sqlalchemy import select

        from src.app.wms_integration.outbound_picking.models import DirectPickExecution

        from .return_rack_repository import member_done_clause

        pending = (
            await db.scalars(
                select(DirectPickExecution).where(
                    DirectPickExecution.picking_task_id == task.id, ~member_done_clause(DirectPickExecution)
                )
            )
        ).all()
        identities = {(str(member.source_evidence_id), member.rack_id, member.rack_face) for member in pending}
        result = []
        for candidate in candidates:
            faces = tuple(
                face
                for face in candidate.rack_faces
                if (candidate.source_evidence_id, candidate.rack_id, face) in identities
            )
            if faces:
                result.append(replace(candidate, rack_faces=faces))
        return tuple(result)

    async def advance_completed_in_session(self, db, line):
        now = self.clock()
        preparing_count = 0
        if self.completion_flow is not None:
            task = await self.tasks.get_active_for_workline(db, line.id)
            if task is not None and task.status == "PREPARING":
                task = await self.tasks.get_by_id_for_update(db, task.id)
                preparing_count = await self.completion_flow.advance_in_session(db, line, task, now=now)
        final_count = await self.final_target_flow.advance_in_session(db, line) if self.final_target_flow else 0
        rack_count = await self.return_rack_flow.advance_in_session(db, line) if self.return_rack_flow else 0
        direct_count = await self.direct_pick_flow.advance_in_session(db, line=line) if self.direct_pick_flow else 0
        drain_phase_count = (
            await self.drain_flow.prepare_return_motion_in_session(db, line) if self.drain_flow is not None else 0
        )
        allocation_count = await self.return_batch_flow.advance_in_session(db, line) if self.return_batch_flow else 0
        motion_count = await self.rack_motion_flow.advance_in_session(db, line) if self.rack_motion_flow else 0
        departure_count = await self.rack_departure_flow.advance_in_session(db, line) if self.rack_departure_flow else 0
        current_returns = await self.discover_returns_in_session(db, line)
        count = (
            await self.exit_flow.advance_returns_in_session(db, line=line, current_returns=current_returns)
            if self.exit_flow is not None
            else 0
        )
        count += (
            preparing_count
            + final_count
            + rack_count
            + direct_count
            + drain_phase_count
            + allocation_count
            + motion_count
            + departure_count
        )
        if self.material_flow is not None:
            count += await self.material_flow.advance_in_session(db, line_id=line.id, now=now)
        if self.target_flow is not None:
            count += await self.target_flow.advance_in_session(db, line_id=line.id, now=now)
        for feed in await self.feeds.list_recoverable(db, line.id):
            snapshot = await self.feeds.inbound(db, feed)
            if snapshot is None:
                continue
            count += await self.advance_feed_in_session(db, line, feed, snapshot)
        for work in await self.works.list_current(db, line.id):
            task = await self.tasks.get_by_id_for_update(db, work.picking_task_id)
            if task is None or task.workline_id != line.id:
                raise ValueError("current Work lost its frozen task owner")
            if work.phase == "PLAN_PENDING":
                snapshot = await self.work_flow.reader.by_operation_id(
                    db, picking_task_id=task.id, operation_id=work.operation_id
                )
                if snapshot is not None and snapshot.status == "COMPLETED":
                    count += await self.works.bind_result_context(db, work, snapshot.evidence_id)
                elif snapshot is not None and snapshot.status == "RECONCILING":
                    count += await self.work_flow.advance_in_session(db, task=task, work=work, now=now)
            else:
                count += await self.work_flow.advance_in_session(db, task=task, work=work, now=now)
            if self.exit_flow is not None:
                count += await self.exit_flow.advance_in_session(db, line=line, work=work, task=task)
            if self.source_flow is not None and work.phase == "READY":
                count += await self.source_flow.advance_in_session(db, work_id=work.id)
        if self.drain_flow is not None:
            count += await self.drain_flow.advance_in_session(db, line)
        return count

    async def advance_feed_in_session(self, db, line, feed, snapshot):
        _intent, result, evidence = snapshot
        count = 0
        if feed.phase == "REQUESTED":
            feed.response_evidence_id = evidence.id
            feed.phase = (
                "FEED_COMPLETE"
                if type(result) is BinInboundBatchRackFaceDone
                else "ALLOCATED"
                if type(result) is BinInboundBatchReady
                else "RECONCILING"
            )
            count += 1
        if type(result) is BinInboundBatchReady:
            count += await self.feed_flow.advance_in_session(db, line=line, feed=feed, ready=result)
        return count

    async def advance_in_session(self, db, line, task):
        count = 0
        if task.status != "EXECUTING" or task.plan_blocked_evidence_id is not None:
            return 0
        if self.direct_pick_flow is not None:
            count += await self.direct_pick_flow.advance_in_session(db, line=line, task=task)
        for member in await self.feeds.source_members(db, task.id):
            if member.cancelled_evidence_id is not None or await self.feeds.by_member(db, member.id) is not None:
                continue
            if await self.feeds.open_for_face(db, line.id, member) is not None:
                continue
            if not await self.feeds.source_ready(db, line, member):
                continue
            operation_id, now = self.uuid_factory(), self.clock()
            await self.feeds.add_feed(
                db, workline_id=line.id, picking_task_id=task.id, source_member_id=member.id, operation_id=operation_id
            )
            await self.batch_scheduler.create_in_session(
                db,
                wms_operations.outbound_bin_inbound_batch(
                    operation_id=operation_id,
                    task_id=task.task_id,
                    rack_id=member.rack_id,
                    rack_face=member.rack_face,
                ),
                workline_id=line.id,
                created_at=now,
            )
            count += 1
        return count

    async def discover_returns_in_session(self, db, line):
        if self.exit_flow is None:
            return []
        return await self.exit_flow.repository.list_current(db, line.id)
