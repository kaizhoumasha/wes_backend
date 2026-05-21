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

    async def get_by_operation_sequence(
        self,
        db: AsyncSession,
        *,
        operation_key: str,
        sequence_no: int,
    ) -> WorklineRackTask | None:
        """按 operation_key + sequence_no 查询单个任务。"""

        columns = cast("Any", WorklineRackTask).__table__.c
        result = await db.execute(
            select(WorklineRackTask).where(
                columns.operation_key == operation_key,
                columns.sequence_no == sequence_no,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_operation_key(
        self,
        db: AsyncSession,
        *,
        operation_key: str,
    ) -> list[WorklineRackTask]:
        """查询同一货架 operation 下的全部任务，按序号升序。"""

        columns = cast("Any", WorklineRackTask).__table__.c
        result = await db.execute(
            select(WorklineRackTask).where(columns.operation_key == operation_key).order_by(columns.sequence_no.asc())
        )
        return list(result.scalars().all())

    async def list_active_by_material_session(
        self,
        db: AsyncSession,
        *,
        material_session_id: int,
    ) -> list[WorklineRackTask]:
        """查询物料 session 当前未闭环的货架任务。"""

        columns = cast("Any", WorklineRackTask).__table__.c
        result = await db.execute(
            select(WorklineRackTask)
            .where(
                columns.material_session_id == material_session_id,
                columns.task_status.in_(_ACTIVE_STATUSES),
            )
            .order_by(columns.operation_key.asc(), columns.sequence_no.asc())
        )
        return list(result.scalars().all())

    async def list_active_by_target_position(
        self,
        db: AsyncSession,
        *,
        workline_code: str,
        target_position_code: str,
    ) -> list[WorklineRackTask]:
        """查询目标位置当前未闭环的货架任务。"""

        columns = cast("Any", WorklineRackTask).__table__.c
        result = await db.execute(
            select(WorklineRackTask)
            .where(
                columns.workline_code == workline_code,
                columns.target_position_code == target_position_code,
                columns.task_status.in_(_ACTIVE_STATUSES),
            )
            .order_by(columns.operation_key.asc(), columns.sequence_no.asc())
        )
        return list(result.scalars().all())

    async def list_open_by_material_session_id(
        self,
        db: AsyncSession,
        material_session_id: int,
    ) -> list[WorklineRackTask]:
        """兼容旧调用：查询物料 session 当前未闭环的货架任务。"""

        return await self.list_active_by_material_session(db, material_session_id=material_session_id)


_ACTIVE_STATUSES = (
    WorklineRackTaskStatus.PLANNED,
    WorklineRackTaskStatus.REQUESTED,
    WorklineRackTaskStatus.IN_PROGRESS,
    WorklineRackTaskStatus.RECONCILING,
)


workline_rack_task_repository = WorklineRackTaskRepository()


__all__ = ["WorklineRackTaskRepository", "workline_rack_task_repository"]
