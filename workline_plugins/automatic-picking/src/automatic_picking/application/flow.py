"""Work 的唯一事务应用路径；READY 授权留给后续取盘切片消费。"""

from datetime import timedelta

from wes_plugin_sdk import BinWorkPlanNoWork, BinWorkPlanReady, BinWorkPlanWait, wms_operations

from src.core.uuid7 import new_uuid7


class AutomaticPickingWorkFlow:
    def __init__(self, repository, scheduler, reader, *, uuid_factory=new_uuid7):
        self.repository = repository
        self.scheduler = scheduler
        self.reader = reader
        self.uuid_factory = uuid_factory

    async def admit_in_session(self, db, *, line_id, task, feed, bin_code, evidence_id, scanned_at, now):
        current = await self.repository.current_work(db, line_id, bin_code)
        if current is not None:
            if current.feed_id != feed.id or current.picking_task_id != task.id:
                raise ValueError("current Work belongs to another frozen batch")
            return current
        operation_id = self.uuid_factory()
        work = await self.repository.add_work(
            db,
            workline_id=line_id,
            picking_task_id=task.id,
            feed_id=feed.id,
            bin_code=bin_code,
            scan2_evidence_id=evidence_id,
            scanned_at=scanned_at,
            operation_id=operation_id,
            phase="PLAN_PENDING",
        )
        await self._request(db, task, work, now)
        return work

    async def _request(self, db, task, work, now):
        await self.scheduler.create_in_session(
            db,
            wms_operations.outbound_bin_work_plan(
                operation_id=work.operation_id, task_id=task.task_id, bin_code=work.bin_code, scanned_at=work.scanned_at
            ),
            picking_task_id=task.id,
            created_at=now,
        )

    async def advance_in_session(self, db, *, task, work, now):
        if work.phase == "WAIT":
            if work.retry_at is None or work.retry_at > now:
                return 0
            work.operation_id = self.uuid_factory()
            work.phase, work.retry_at, work.response_evidence_id = "PLAN_PENDING", None, None
            await self._request(db, task, work, now)
            return 1
        if work.phase != "PLAN_PENDING":
            return 0
        snapshot = await self.reader.by_operation_id(db, picking_task_id=task.id, operation_id=work.operation_id)
        if snapshot is None:
            return 0
        intent = snapshot.intent
        if (
            intent.operation_id != work.operation_id
            or intent.task_id != task.task_id
            or intent.bin_code != work.bin_code
            or intent.scanned_at != work.scanned_at
        ):
            raise ValueError("work plan result differs from frozen Work")
        if snapshot.status == "RECONCILING":
            work.phase = "RECONCILING"
            return 1
        if snapshot.status != "COMPLETED" or snapshot.outcome is None:
            return 0
        result = snapshot.outcome.result
        work.response_evidence_id = snapshot.evidence_id
        if type(result) is BinWorkPlanReady:
            work.phase, work.authorization_evidence_id = "READY", snapshot.evidence_id
        elif type(result) is BinWorkPlanNoWork:
            work.phase = "EXIT_PENDING"
        elif type(result) is BinWorkPlanWait:
            if snapshot.completed_at is None:
                raise ValueError("completed WAIT has no durable completion time")
            work.phase = "WAIT"
            work.retry_at = snapshot.completed_at + timedelta(milliseconds=result.retry_after_ms)
        else:
            work.phase = "RECONCILING"
        return 1
