"""Material V1 API 导出。"""

from fastapi import APIRouter

from .material_unit import router as material_unit_router

router = APIRouter()
router.include_router(material_unit_router)

__all__ = ["router"]
