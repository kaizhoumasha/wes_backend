"""Device Service 层"""

from src.app.device.models import Device
from src.app.device.repositories import DeviceRepository, device_repository
from src.core.base_service import BaseService


class DeviceService(BaseService[Device, DeviceRepository]):
    """设备业务逻辑层"""

    def __init__(self) -> None:
        super().__init__(
            device_repository,
            enable_cache=True,
            cache_prefix="app:device:detail",
        )


# 创建单例
device_service = DeviceService()
