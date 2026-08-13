"""设备静态主数据 API。"""

from src.app.device.models.device import Device, DeviceCreate, DeviceResponse, DeviceUpdate
from src.app.device.services.device_service import device_service
from src.core.base_api import BaseAPI

device_api = BaseAPI(
    module_name="biz",
    model=Device,
    service=device_service,
    create_schema=DeviceCreate,
    update_schema=DeviceUpdate,
    response_schema=DeviceResponse,
    prefix="/devices",
    tags=["设备管理"],
)

router = device_api.router

__all__ = ["router"]
