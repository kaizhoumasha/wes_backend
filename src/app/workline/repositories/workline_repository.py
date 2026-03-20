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


# 创建单例
workline_repository = WorkLineRepository()
