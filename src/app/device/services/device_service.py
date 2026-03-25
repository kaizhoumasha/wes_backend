"""Device Service 层"""

from typing import TYPE_CHECKING

from src.app.device.models import Device
from src.app.device.repositories import DeviceRepository, device_repository
from src.common.cache_config import cache_settings
from src.core.base_service import BaseService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class DeviceService(BaseService[Device, DeviceRepository]):
    """设备业务逻辑层"""

    def __init__(self) -> None:
        super().__init__(
            device_repository,
            enable_cache=True,
            cache_prefix=cache_settings.DEVICE.prefix,
            cache_expire=cache_settings.DEVICE.expire,
            list_cache_prefix=cache_settings.DEVICE_LIST.prefix,
            list_cache_expire=cache_settings.DEVICE_LIST.expire,
        )

    async def get_device_by_code(self, db: "AsyncSession", device_code: str) -> Device | None:
        """根据 device_code 查询设备。"""
        return await self.repo.get_by_device_code(db, device_code)


# 创建单例
device_service = DeviceService()
