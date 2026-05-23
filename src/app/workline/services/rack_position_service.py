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

    def _require_enabled_position(
        self,
        position: WorklineRackPosition | None,
        *,
        workline_code: str,
        position_code: str,
        rack_kind: RackKind | None = None,
        require_capacity: bool = False,
    ) -> WorklineRackPosition:
        """统一校验停靠位存在、启用、货架类型和容量规则。"""

        if position is None:
            raise ValueError(f"workline rack position not found: {workline_code}/{position_code}")
        if not position.enabled:
            raise ValueError(f"workline rack position disabled: {workline_code}/{position_code}")
        if rack_kind is not None:
            allowed = position.allowed_rack_kind
            if allowed != rack_kind:
                raise ValueError(f"allowed rack kind mismatch: expected {allowed}, got {rack_kind}")
        if require_capacity:
            _ = self._require_capacity_value(position, workline_code=workline_code, position_code=position_code)
        return position

    def _require_capacity_value(
        self,
        position: WorklineRackPosition,
        *,
        workline_code: str,
        position_code: str,
    ) -> int:
        """统一读取并校验停靠位容量。"""

        if position.capacity is None or position.capacity < 1:
            raise ValueError(f"workline rack position capacity invalid: {workline_code}/{position_code}")
        return position.capacity

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
        return self._require_enabled_position(
            position,
            workline_code=workline_code,
            position_code=position_code,
            rack_kind=rack_kind,
        )

    async def require_enabled_position_for_update(
        self,
        db: AsyncSession,
        *,
        workline_code: str,
        position_code: str,
        rack_kind: RackKind,
    ) -> WorklineRackPosition:
        """校验工作线停靠位，并对目标配置行加锁以串行化容量占用。"""

        position = await self.repository.get_by_workline_position_for_update(
            db,
            workline_code=workline_code,
            position_code=position_code,
        )
        return self._require_enabled_position(
            position,
            workline_code=workline_code,
            position_code=position_code,
            rack_kind=rack_kind,
            require_capacity=True,
        )

    async def require_position_capacity(
        self,
        db: AsyncSession,
        *,
        workline_code: str,
        position_code: str,
    ) -> int:
        """读取启用停靠位容量；缺失或禁用时抛出明确异常。"""

        position = await self.repository.get_by_workline_position(
            db,
            workline_code=workline_code,
            position_code=position_code,
        )
        position = self._require_enabled_position(
            position,
            workline_code=workline_code,
            position_code=position_code,
            require_capacity=True,
        )
        return self._require_capacity_value(position, workline_code=workline_code, position_code=position_code)

    async def require_position_capacity_for_update(
        self,
        db: AsyncSession,
        *,
        workline_code: str,
        position_code: str,
        rack_kind: RackKind,
    ) -> tuple[WorklineRackPosition, int]:
        """加锁读取启用停靠位及容量，用于串行化位置容量占用。"""

        position = await self.repository.get_by_workline_position_for_update(
            db,
            workline_code=workline_code,
            position_code=position_code,
        )
        position = self._require_enabled_position(
            position,
            workline_code=workline_code,
            position_code=position_code,
            rack_kind=rack_kind,
            require_capacity=True,
        )
        capacity = self._require_capacity_value(position, workline_code=workline_code, position_code=position_code)
        return position, capacity


workline_rack_position_service = WorklineRackPositionService()


__all__ = ["WorklineRackPositionService", "workline_rack_position_service"]
