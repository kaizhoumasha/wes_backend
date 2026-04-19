"""Device Service 层"""

from typing import TYPE_CHECKING, Any

from src.app.device.models import Device, parse_device_capabilities
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

    @staticmethod
    def _resolve_work_line_id(device: Device | None) -> int | None:
        return getattr(device, "work_line_id", None) if device else None

    async def get_device_by_code(self, db: "AsyncSession", device_code: str) -> Device | None:
        """根据 device_code 查询设备。"""
        return await self.repo.get_by_device_code(db, device_code)

    async def create(
        self,
        db: "AsyncSession",
        data: dict[str, Any],
        cache: object | None = None,
    ) -> Device | None:
        """创建设备前校验 capability schema。"""

        self._validate_capabilities(data)
        return await super().create(db, data, cache)

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
        old_work_line_id = self._resolve_work_line_id(old_device)

        self._validate_capabilities(data, current=old_device)

        # 执行更新
        updated_device = await super().update(db, id, data, cache)

        if updated_device:
            new_work_line_id = self._resolve_work_line_id(updated_device)
            # 比较 work_line_id 变化，失效进程内缓存
            if old_work_line_id != new_work_line_id:
                await self.repo.after_device_change(db, old_work_line_id, new_work_line_id)

        return updated_device

    @staticmethod
    def _validate_capabilities(data: dict[str, Any], current: Device | None = None) -> None:
        """校验设备能力声明结构，保持 schema 轻量且稳定。"""

        if "capabilities_json" not in data and current is None:
            return

        raw_value = data.get("capabilities_json", getattr(current, "capabilities_json", None))
        _ = parse_device_capabilities(raw_value)


# 创建单例
device_service = DeviceService()
