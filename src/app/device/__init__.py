from fastapi import APIRouter

from .v1 import router as device_router

router_v1 = APIRouter(prefix="/v1")

# API v1 路由
router_v1.include_router(device_router)

__all__ = ["router_v1"]
