"""WorkLine V1 API 导出"""

from fastapi import APIRouter

from .active_objects import router as active_objects_router
from .operation import router as operation_router
from .workline import router as workline_router

router = APIRouter()
router.include_router(workline_router)
router.include_router(operation_router, prefix="/operations")
router.include_router(active_objects_router)

__all__ = ["router"]
