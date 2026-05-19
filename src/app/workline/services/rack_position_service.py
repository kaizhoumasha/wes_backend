"""工作线货架停靠位服务。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.app.workline.repositories.rack_position_repository import (
    WorklineRackPositionRepository,
    workline_rack_position_repository,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.resource.models import RackKind
    from src.app.workline.models.rack_position import WorklineRackPosition


class WorklineRackPositionService:
    """工作线货架停靠位配置校验服务。"""

    def __init__(
        self,
        *,
        repository: WorklineRackPositionRepository = workline_rack_position_repository,
    ) -> None:
        self.repository = repository

    async def require_enabled_position(
        self,
        db: AsyncSession,
        *,
        workline_code: str,
        position_code: str,
        rack_kind: RackKind,
    ) -> WorklineRackPosition:
        """校验工作线停靠位存在、启用且允许指定货架类型。"""

        position = await self.repository.get_by_workline_position(
            db,
            workline_code=workline_code,
            position_code=position_code,
        )
        if position is None:
            raise ValueError(f"workline rack position not found: {workline_code}/{position_code}")
        if not position.enabled:
            raise ValueError(f"workline rack position disabled: {workline_code}/{position_code}")
        allowed = position.allowed_rack_kind
        if allowed != rack_kind:
            raise ValueError(f"allowed rack kind mismatch: expected {allowed}, got {rack_kind}")
        if position.capacity != 1:
            raise ValueError("workline rack position capacity must be 1 in Phase A")
        return position


workline_rack_position_service = WorklineRackPositionService()


__all__ = ["WorklineRackPositionService", "workline_rack_position_service"]
