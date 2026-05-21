"""WorkLine Repository 层"""

from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.workline.models import WorkLine
from src.database.base_repository import BaseRepository


class WorkLineRepository(BaseRepository[WorkLine]):
    """作业线数据访问层"""

    def __init__(self) -> None:
        """初始化作业线仓库"""
        super().__init__(WorkLine)

    async def get_by_line_code(
        self,
        db: AsyncSession,
        line_code: str,
    ) -> WorkLine | None:
        """根据作业线编码查询"""
        columns = cast("Any", WorkLine).__table__.c
        result = await db.execute(
            select(WorkLine).where(
                columns.line_code == line_code,
                columns.is_deleted.is_(False),
            )
        )
        return result.scalar_one_or_none()

    async def get_for_update(
        self,
        db: AsyncSession,
        workline_id: int,
    ) -> WorkLine | None:
        """根据 ID 查询并锁定 WorkLine，用于安全状态切换。"""

        columns = cast("Any", WorkLine).__table__.c
        result = await db.execute(
            select(WorkLine)
            .where(
                columns.id == workline_id,
                columns.is_deleted.is_(False),
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()


# 创建单例
workline_repository = WorkLineRepository()
