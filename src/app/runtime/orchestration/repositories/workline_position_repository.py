"""工作线工作位 Repository。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select

from src.app.resource.repositories.resource_repository import bin_placement_repository, rack_placement_repository
from src.app.runtime.orchestration.models.workline_position import WorkLinePosition
from src.database.base_repository import BaseRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.workline.models.workline import WorkLine, WorkLinePositionInput


class WorkLinePositionRepository(BaseRepository[WorkLinePosition]):
    """工作线工作位 Repository。"""

    def __init__(self) -> None:
        super().__init__(WorkLinePosition)

    async def has_active_placements(self, db: AsyncSession, workline_id: int) -> bool:
        """基础配置变更前复用资源投影查询，包含未知但尚未离位的关系。"""
        for repository in (rack_placement_repository, bin_placement_repository):
            summary = await repository.get_active_workline_summary(db, workline_id)
            if summary["count"] > 0:
                return True
        return False

    async def list_for_workline(
        self, db: AsyncSession, workline_id: int, *, for_update: bool = False
    ) -> list[WorkLinePosition]:
        columns = cast("Any", WorkLinePosition).__table__.c
        statement = select(WorkLinePosition).where(columns.workline_id == workline_id).order_by(columns.position_code)
        if for_update:
            statement = statement.with_for_update()
        return list((await db.execute(statement)).scalars().all())

    async def replace_for_workline(
        self,
        db: AsyncSession,
        *,
        workline: WorkLine,
        positions: tuple[WorkLinePositionInput, ...],
        existing: list[WorkLinePosition],
    ) -> None:
        """调用者持有工作线与既有位置锁；保持未变位置身份和扩展属性，不提交事务。"""
        if workline.id is None:
            raise ValueError("工作线必须先持久化")
        by_code = {position.position_code: position for position in existing}
        for draft in positions:
            position = by_code.pop(draft.position_code, None)
            if position is None:
                position = WorkLinePosition(
                    workline_id=workline.id, workline_code=workline.line_code, **draft.model_dump()
                )
            else:
                for key, value in draft.model_dump().items():
                    setattr(position, key, value)
                position.workline_code = workline.line_code
            db.add(position)
        for position in by_code.values():
            await db.delete(position)

    async def get_by_workline_position(
        self,
        db: AsyncSession,
        *,
        workline_code: str,
        position_code: str,
    ) -> WorkLinePosition | None:
        """按工作线和停靠位查询配置。"""

        columns = cast("Any", WorkLinePosition).__table__.c
        result = await db.execute(
            select(WorkLinePosition).where(
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
    ) -> WorkLinePosition | None:
        """按工作线和停靠位查询配置，并对目标行加行级锁。"""

        columns = cast("Any", WorkLinePosition).__table__.c
        result = await db.execute(
            select(WorkLinePosition)
            .where(
                columns.workline_code == workline_code,
                columns.position_code == position_code,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_by_workline_logic_location(
        self,
        db: AsyncSession,
        *,
        workline_code: str,
        logic_location_code: str,
    ) -> WorkLinePosition | None:
        """按 WorkLine 冻结逻辑位置精确解析一个工作位；重复配置失败关闭。"""

        columns = cast("Any", WorkLinePosition).__table__.c
        result = await db.execute(
            select(WorkLinePosition).where(
                columns.workline_code == workline_code,
                columns.logic_location_code == logic_location_code,
                columns.enabled.is_(True),
            )
        )
        return result.scalar_one_or_none()


workline_position_repository = WorkLinePositionRepository()


__all__ = ["WorkLinePositionRepository", "workline_position_repository"]
