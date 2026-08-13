"""Device V1 API 导出。"""

from fastapi import APIRouter

from .device import router as device_router
from .ecs_callback import router as ecs_callback_router

router = APIRouter()
router.include_router(device_router)
router.include_router(ecs_callback_router, prefix="/callback")

__all__ = ["router"]
