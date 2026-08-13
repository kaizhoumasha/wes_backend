"""Callback 模块入口。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router_v1: APIRouter

__all__ = ["router_v1"]


def __getattr__(name: str) -> Any:
    if name != "router_v1":
        raise AttributeError(name)
    from .v1 import router as callback_router

    router_v1 = APIRouter(prefix="/v1/callback", tags=["Callback"])
    router_v1.include_router(callback_router)
    globals()["router_v1"] = router_v1
    return router_v1
