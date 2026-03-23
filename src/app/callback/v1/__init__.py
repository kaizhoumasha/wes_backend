"""Callback API v1 模块."""

from fastapi import APIRouter

from src.app.callback.v1.callback import router as callback_router
from src.app.callback.v1.callback_log import router as callback_log_router

router_v1 = APIRouter(prefix="/v1/callback", tags=["Callback"])

# 注册回调路由
router_v1.include_router(callback_router)
# 注册回调日志查询路由
router_v1.include_router(callback_log_router, prefix="/logs")

__all__ = ["router_v1"]
