"""MaterialUnit Repository 层。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select

from src.app.runtime.orchestration.models.material_unit import MaterialUnit
from src.database.base_repository import BaseRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class MaterialUnitRepository(BaseRepository[MaterialUnit]):
    """料盘根实体数据访问层。"""

    def __init__(self) -> None:
        super().__init__(MaterialUnit)

    async def get_by_pkg_code(self, db: AsyncSession, pkg_code: str) -> MaterialUnit | None:
        """按 pkg_code 查询料盘根实体。"""

        columns = cast("Any", MaterialUnit).__table__.c
        result = await db.execute(select(MaterialUnit).where(columns.pkg_code == pkg_code).limit(1))
        return result.scalar_one_or_none()

    async def get_by_pkg_code_for_update(self, db: AsyncSession, pkg_code: str) -> MaterialUnit | None:
        """锁定已存在料盘直到外层事务结束，避免并发 Session 静默窃取所有权。"""

        columns = cast("Any", MaterialUnit).__table__.c
        result = await db.execute(select(MaterialUnit).where(columns.pkg_code == pkg_code).limit(1).with_for_update())
        return result.scalar_one_or_none()

    async def update_current_location_by_pkg_code(
        self,
        db: AsyncSession,
        *,
        pkg_code: str,
        current_location: str | None,
    ) -> MaterialUnit | None:
        """按 pkg_code 更新当前位置缓存，不改变料盘状态。"""

        material_unit = await self.get_by_pkg_code(db, pkg_code)
        if material_unit is None:
            return None
        material_unit.current_location = current_location
        db.add(material_unit)
        return material_unit


material_unit_repository = MaterialUnitRepository()


__all__ = ["MaterialUnitRepository", "material_unit_repository"]
