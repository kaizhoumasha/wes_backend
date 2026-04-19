"""WorkLine V1 API 导出"""

from fastapi import APIRouter

from .runtime import router as runtime_router
from .trace import router as trace_router
from .workline import router as workline_router

router = APIRouter()
router.include_router(workline_router)
router.include_router(trace_router, prefix="/trace")
router.include_router(runtime_router, prefix="/runtime")

__all__ = ["router"]
