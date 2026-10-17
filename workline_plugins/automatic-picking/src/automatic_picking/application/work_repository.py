"""Work 当前轮次的持久查询；工作线锁由宿主/业务消费者统一取得。"""

from sqlalchemy import func, select
from sqlmodel import col

from src.app.execution.models import InboundEvidence

from .feed_model import AutomaticPickingFeed
from .feed_repository import FeedRepository
from .return_allocation_model import AutomaticPickingReturnAllocation
from .return_model import AutomaticPickingReturn
from .return_motion_model import AutomaticPickingReturnMotion
from .work_model import AutomaticPickingWork


class WorkRepository:
    async def current_work(self, db, line_id, bin_code):
        return await db.scalar(
            select(AutomaticPickingWork)
            .where(
                col(AutomaticPickingWork.workline_id) == line_id,
                col(AutomaticPickingWork.bin_code) == bin_code,
                col(AutomaticPickingWork.phase) != "CLOSED",
                col(AutomaticPickingWork.archived_at).is_(None),
            )
            .with_for_update()
        )

    async def add_work(self, db, **values):
        work = AutomaticPickingWork(**values)
        db.add(work)
        await db.flush()
        return work

    async def list_current(self, db, line_id):
        return list(
            (
                await db.scalars(
                    select(AutomaticPickingWork)
                    .where(
                        col(AutomaticPickingWork.workline_id) == line_id,
                        col(AutomaticPickingWork.phase) != "CLOSED",
                        col(AutomaticPickingWork.archived_at).is_(None),
                    )
                    .order_by(col(AutomaticPickingWork.id))
                    .with_for_update()
                )
            ).all()
        )

    async def get_unfinished_workload_summary(self, db, workline_id):
        work_count = await db.scalar(
            select(func.count())
            .select_from(AutomaticPickingWork)
            .where(
                col(AutomaticPickingWork.workline_id) == workline_id,
                col(AutomaticPickingWork.archived_at).is_(None),
                col(AutomaticPickingWork.phase) != "CLOSED",
            )
        )
        return_count = await db.scalar(
            select(func.count())
            .select_from(AutomaticPickingReturn)
            .where(
                col(AutomaticPickingReturn.workline_id) == workline_id,
                col(AutomaticPickingReturn.archived_at).is_(None),
            )
        )
        allocation_count = await db.scalar(
            select(func.count())
            .select_from(AutomaticPickingReturnAllocation)
            .where(
                AutomaticPickingReturnAllocation.workline_id == workline_id,
                AutomaticPickingReturnAllocation.archived_at.is_(None),
                AutomaticPickingReturnAllocation.settled_at.is_(None),
            )
        )
        motion_count = await db.scalar(
            select(func.count())
            .select_from(AutomaticPickingReturnMotion)
            .where(
                AutomaticPickingReturnMotion.workline_id == workline_id,
                AutomaticPickingReturnMotion.archived_at.is_(None),
                AutomaticPickingReturnMotion.closed_at.is_(None),
            )
        )
        count = (
            work_count
            + return_count
            + allocation_count
            + motion_count
            + len(await FeedRepository().list_admission_pending(db, workline_id))
        )
        return {"count": count, "sample": "automatic-picking current Work/feed/Return" if count else None}

    async def archive_open_work(self, db, *, workline_id, archived_at):
        count = 0
        for model in (
            AutomaticPickingWork,
            AutomaticPickingFeed,
            AutomaticPickingReturn,
            AutomaticPickingReturnAllocation,
            AutomaticPickingReturnMotion,
        ):
            rows = (
                await db.scalars(
                    select(model)
                    .where(col(model.workline_id) == workline_id, col(model.archived_at).is_(None))
                    .with_for_update()
                )
            ).all()
            for row in rows:
                row.archived_at = archived_at
                count += 1
        await db.flush()
        return count

    async def bind_result_context(self, db, work, evidence_id):
        evidence = await db.get(InboundEvidence, evidence_id)
        if (
            evidence is None
            or evidence.kind != "WMS_RESULT"
            or evidence.apply_status != "APPLIED"
            or evidence.material_execution_id is not None
            or evidence.picking_task_id != work.picking_task_id
            or evidence.operation != "outbound.bin.work_plan@v1"
            or evidence.operation_id != work.operation_id
            or evidence.workline_id not in (None, work.workline_id)
        ):
            raise ValueError("Work response cannot bind to another business context")
        # PickingTask owner 与 WorkLine 路由上下文分离，后者只由原 Work 恢复。
        changed = evidence.workline_id is None
        evidence.workline_id = work.workline_id
        return int(changed)

    async def by_operation(self, db, operation_id):
        return await db.scalar(
            select(AutomaticPickingWork).where(col(AutomaticPickingWork.operation_id) == operation_id).with_for_update()
        )
