"""Callback API v1 模块."""

from fastapi import APIRouter

from src.app.callback.v1.callback import router as callback_router

router_v1 = APIRouter(prefix="/v1/callback", tags=["Callback"])

# 注册回调路由
router_v1.include_router(callback_router)

__all__ = ["router_v1"]
