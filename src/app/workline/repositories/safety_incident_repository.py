"""WorkLine 安全事件 Repository 层。"""

from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.workline.models.safety import WorklineSafetyIncident, WorklineSafetyIncidentStatus
from src.database.base_repository import BaseRepository


class WorklineSafetyIncidentRepository(BaseRepository[WorklineSafetyIncident]):
    """WorkLine 安全事件数据访问层。"""

    def __init__(self) -> None:
        """初始化安全事件仓库。"""
        super().__init__(WorklineSafetyIncident)

    async def get_active_for_workline(
        self,
        db: AsyncSession,
        workline_id: int,
    ) -> WorklineSafetyIncident | None:
        """查询 WorkLine 当前生效的安全事件。"""

        columns = cast("Any", WorklineSafetyIncident).__table__.c
        result = await db.execute(
            select(WorklineSafetyIncident)
            .where(
                columns.workline_id == workline_id,
                columns.status == WorklineSafetyIncidentStatus.ACTIVE,
            )
            .order_by(columns.created_at.desc(), columns.id.desc())
            .limit(1)
            .with_for_update()
        )
        return result.scalar_one_or_none()


workline_safety_incident_repository = WorklineSafetyIncidentRepository()


__all__ = [
    "WorklineSafetyIncidentRepository",
    "workline_safety_incident_repository",
]
