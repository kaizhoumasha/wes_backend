"""设备静态主数据 Service。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.app.device.models.device import Device
from src.app.device.repositories.device_repository import DeviceRepository, device_repository
from src.common.cache_config import cache_settings
from src.core.base_service import BaseService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class DeviceService(BaseService[Device, DeviceRepository]):
    """只维护设备身份和物理拓扑，不写 ECS 运行态。"""

    TOPOLOGY_FIELDS = frozenset(
        {
            "device_code",
            "work_line_id",
            "device_role",
            "role_index",
            "upstream_device_id",
            "sort_order",
        }
    )

    def __init__(self) -> None:
        super().__init__(
            device_repository,
            enable_cache=True,
            cache_prefix=cache_settings.DEVICE.prefix,
            cache_expire=cache_settings.DEVICE.expire,
            list_cache_prefix=cache_settings.DEVICE_LIST.prefix,
            list_cache_expire=cache_settings.DEVICE_LIST.expire,
        )

    async def get_device_by_code(self, db: AsyncSession, device_code: str) -> Device | None:
        return await self.repo.get_by_device_code(db, device_code)


device_service = DeviceService()

__all__ = ["DeviceService", "device_service"]
