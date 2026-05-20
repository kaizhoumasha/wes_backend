"""工作线货架级任务 Repository。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select

from src.app.workline.models.rack_task import WorklineRackTask, WorklineRackTaskStatus
from src.database.base_repository import BaseRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class WorklineRackTaskRepository(BaseRepository[WorklineRackTask]):
    """工作线货架级任务 Repository。"""

    def __init__(self) -> None:
        super().__init__(WorklineRackTask)

    async def get_by_task_key(self, db: AsyncSession, task_key: str) -> WorklineRackTask | None:
        """按任务幂等键查询。"""

        columns = cast("Any", WorklineRackTask).__table__.c
        result = await db.execute(select(WorklineRackTask).where(columns.task_key == task_key))
        return result.scalar_one_or_none()

    async def get_by_dispatch_key(self, db: AsyncSession, dispatch_key: str) -> WorklineRackTask | None:
        """按外部派发键查询。"""

        columns = cast("Any", WorklineRackTask).__table__.c
        result = await db.execute(select(WorklineRackTask).where(columns.dispatch_key == dispatch_key))
        return result.scalar_one_or_none()

    async def list_open_by_material_session_id(
        self,
        db: AsyncSession,
        material_session_id: int,
    ) -> list[WorklineRackTask]:
        """查询物料 session 当前未闭环的货架任务。"""

        columns = cast("Any", WorklineRackTask).__table__.c
        open_statuses = (
            WorklineRackTaskStatus.PLANNED,
            WorklineRackTaskStatus.REQUESTED,
            WorklineRackTaskStatus.IN_PROGRESS,
            WorklineRackTaskStatus.RECONCILING,
        )
        result = await db.execute(
            select(WorklineRackTask)
            .where(
                columns.material_session_id == material_session_id,
                columns.task_status.in_(open_statuses),
            )
            .order_by(columns.id.asc())
        )
        return list(result.scalars().all())


workline_rack_task_repository = WorklineRackTaskRepository()


__all__ = ["WorklineRackTaskRepository", "workline_rack_task_repository"]
