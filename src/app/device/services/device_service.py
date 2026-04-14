"""Device Service 层"""

from typing import TYPE_CHECKING, Any

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

    async def update(
        self,
        db: "AsyncSession",
        id: int,
        data: dict[str, Any],
        cache: object | None = None,
    ) -> Device | None:
        """更新设备后失效工作线设备缓存（内存缓存）"""
        # 先获取旧设备信息
        old_device = await self.repo.get_by_id(db, id)
        old_work_line_id = getattr(old_device, "work_line_id", None) if old_device else None

        # 执行更新
        updated_device = await super().update(db, id, data, cache)

        if updated_device:
            new_work_line_id = getattr(updated_device, "work_line_id", None)
            # 比较 work_line_id 变化，失效进程内缓存
            if old_work_line_id != new_work_line_id:
                await self.repo.after_device_change(db, old_work_line_id, new_work_line_id)

        return updated_device


# 创建单例
device_service = DeviceService()
