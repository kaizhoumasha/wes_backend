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
        uuid_factory=new_uuid7,
        clock=timezone.now_for_db,
    ):
        self.works, self.feeds = works, feeds
        self.work_flow, self.feed_flow = work_flow, feed_flow
        self.batch_scheduler = batch_scheduler
        self.tasks = tasks or PickingTaskRepository()
        self.source_flow = source_flow
        self.uuid_factory, self.clock = uuid_factory, clock

    async def advance_completed_in_session(self, db, line):
        now = self.clock()
        count = 0
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
            if self.source_flow is not None and work.phase == "READY":
                count += await self.source_flow.advance_in_session(db, work_id=work.id)
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
        for member in await self.feeds.source_members(db, task.id):
            if member.cancelled_evidence_id is not None or await self.feeds.by_member(db, member.id) is not None:
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
                    plan_revision=member.plan_revision,
                    rack_id=member.rack_id,
                    rack_face=member.rack_face,
                ),
                workline_id=line.id,
                created_at=now,
            )
            count += 1
        return count
