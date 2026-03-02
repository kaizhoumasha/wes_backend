from fastapi import APIRouter

from src.app.device.v1.callback import router as callback_router
from src.app.device.v1.device import router as device_router

router_v1 = APIRouter(prefix="/v1")

# 注册路由
router_v1.include_router(callback_router)  # 设备回调
router_v1.include_router(device_router)  # 设备管理

__ALL__ = ["router_v1"]
