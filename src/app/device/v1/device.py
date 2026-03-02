"""Device API 路由"""

from src.app.device.models import (
    Device,
    DeviceCreate,
    DeviceResponse,
    DeviceUpdate,
)
from src.app.device.services import device_service
from src.core.base_api import BaseAPI

# 使用 BaseAPI 零代码生成 CRUD 路由
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
