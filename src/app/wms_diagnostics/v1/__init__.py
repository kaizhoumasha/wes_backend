"""WMS 近期诊断与持久化可靠事实的只读 API。"""

from fastapi import APIRouter

from .exchanges import router as exchanges_router
from .execution import router as execution_router

router = APIRouter()
router.include_router(exchanges_router)
router.include_router(execution_router)
__all__ = ["router"]
