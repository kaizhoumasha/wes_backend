"""统一 PickingTask 队列持久化。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select, text

from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskStatus
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


picking_task_repository = PickingTaskRepository()

__all__ = ["PickingTaskRepository", "picking_task_repository"]
