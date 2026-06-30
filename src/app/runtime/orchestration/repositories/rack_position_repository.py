"""工作线货架停靠位 Repository。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select

from src.app.runtime.orchestration.models.rack_position import WorklineRackPosition
from src.database.base_repository import BaseRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class WorklineRackPositionRepository(BaseRepository[WorklineRackPosition]):
    """工作线货架停靠位 Repository。"""

    def __init__(self) -> None:
        super().__init__(WorklineRackPosition)

    async def get_by_workline_position(
        self,
        db: AsyncSession,
        *,
        workline_code: str,
        position_code: str,
    ) -> WorklineRackPosition | None:
        """按工作线和停靠位查询配置。"""

        columns = cast("Any", WorklineRackPosition).__table__.c
        result = await db.execute(
            select(WorklineRackPosition).where(
                columns.workline_code == workline_code,
                columns.position_code == position_code,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_workline_position_for_update(
        self,
        db: AsyncSession,
        *,
        workline_code: str,
        position_code: str,
    ) -> WorklineRackPosition | None:
        """按工作线和停靠位查询配置，并对目标行加行级锁。"""

        columns = cast("Any", WorklineRackPosition).__table__.c
        result = await db.execute(
            select(WorklineRackPosition)
            .where(
                columns.workline_code == workline_code,
                columns.position_code == position_code,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()


workline_rack_position_repository = WorklineRackPositionRepository()


__all__ = ["WorklineRackPositionRepository", "workline_rack_position_repository"]
