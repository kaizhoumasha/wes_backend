"""Callback V1 API 导出。"""

from fastapi import APIRouter

from .callback import router as callback_router
from .callback_log import router as callback_log_router

router = APIRouter()
router.include_router(callback_router)
router.include_router(callback_log_router)

__all__ = ["router"]
