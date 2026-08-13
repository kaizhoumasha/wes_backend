"""Device 模块入口。"""

from typing import Any

from fastapi import APIRouter

router_v1: APIRouter

__all__ = ["router_v1"]


def __getattr__(name: str) -> Any:
    """按需构建 router，避免包导入时拉起整条 API 链。"""

    if name != "router_v1":
        raise AttributeError(name)

    from .v1.device import router as device_router
    from .v1.ecs_callback import router as ecs_callback_router

    router_v1 = APIRouter()
    router_v1.include_router(device_router, prefix="/v1/device")
    router_v1.include_router(ecs_callback_router, prefix="/v1/callback", tags=["Device ECS Callback"])
    globals()["router_v1"] = router_v1
    return router_v1
