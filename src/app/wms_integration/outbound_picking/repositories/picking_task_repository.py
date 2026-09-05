"""统一 PickingTask 队列持久化。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import or_, select, text

from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskStatus, PickingTaskType
from src.database.base_repository import BaseRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class PickingTaskRepository(BaseRepository[PickingTask]):
    def __init__(self) -> None:
        super().__init__(PickingTask)

    async def lock_task_identity(self, db: AsyncSession, task_id: str) -> None:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
            {"identity": f"picking-task:{task_id}"},
        )

    async def lock_dispatch_sequence(self, db: AsyncSession, dispatch_sequence: int) -> None:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
            {"identity": f"picking-task-dispatch:{dispatch_sequence}"},
        )

    async def get_by_task_id_for_update(self, db: AsyncSession, task_id: str) -> PickingTask | None:
        columns = cast("Any", PickingTask).__table__.c
        return await db.scalar(select(PickingTask).where(columns.task_id == task_id).with_for_update())

    async def get_by_id_for_update(self, db: AsyncSession, picking_task_id: int) -> PickingTask | None:
        columns = cast("Any", PickingTask).__table__.c
        return await db.scalar(select(PickingTask).where(columns.id == picking_task_id).with_for_update())

    async def get_queued_by_dispatch_sequence_for_update(
        self,
        db: AsyncSession,
        dispatch_sequence: int,
    ) -> PickingTask | None:
        columns = cast("Any", PickingTask).__table__.c
        return await db.scalar(
            select(PickingTask)
            .where(
                columns.dispatch_sequence == dispatch_sequence,
                columns.status == PickingTaskStatus.QUEUED,
            )
            .with_for_update()
        )

    async def add(self, db: AsyncSession, task: PickingTask) -> PickingTask:
        db.add(task)
        await db.flush()
        return task

    async def has_active_for_workline(self, db: AsyncSession, workline_id: int) -> bool:
        columns = cast("Any", PickingTask).__table__.c
        task_id = await db.scalar(
            select(columns.id)
            .where(
                columns.workline_id == workline_id,
                columns.status.in_((PickingTaskStatus.PREPARING, PickingTaskStatus.EXECUTING)),
            )
            .limit(1)
        )
        return task_id is not None

    async def claim_next_manual(self, db: AsyncSession, *, now_ms: int) -> PickingTask | None:
        columns = cast("Any", PickingTask).__table__.c
        return await db.scalar(
            select(PickingTask)
            .where(
                columns.status == PickingTaskStatus.QUEUED,
                columns.task_type == PickingTaskType.MANUAL,
                or_(columns.not_before_ms.is_(None), columns.not_before_ms <= now_ms),
            )
            .order_by(columns.dispatch_sequence, columns.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )

    async def flush(self, db: AsyncSession) -> None:
        await db.flush()


picking_task_repository = PickingTaskRepository()

__all__ = ["PickingTaskRepository", "picking_task_repository"]
