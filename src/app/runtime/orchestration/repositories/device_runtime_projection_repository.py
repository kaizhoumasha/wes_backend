"""DeviceRuntimeProjection Repository 层。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select

from src.app.runtime.orchestration.device_runtime_projection import DeviceRuntimeProjection
from src.database.base_repository import BaseRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class DeviceRuntimeProjectionRepository(BaseRepository[DeviceRuntimeProjection]):
    """设备运行态投影数据访问层。"""

    def __init__(self) -> None:
        super().__init__(DeviceRuntimeProjection)

    async def get_by_device_code(
        self,
        db: AsyncSession,
        device_code: str,
    ) -> DeviceRuntimeProjection | None:
        """按设备业务编码读取运行态投影。"""

        columns = cast("Any", DeviceRuntimeProjection).__table__.c
        result = await db.execute(
            select(DeviceRuntimeProjection).where(columns.device_code == device_code).order_by(columns.id.asc())
        )
        return result.scalar_one_or_none()


device_runtime_projection_repository = DeviceRuntimeProjectionRepository()


__all__ = [
    "DeviceRuntimeProjectionRepository",
    "device_runtime_projection_repository",
]
