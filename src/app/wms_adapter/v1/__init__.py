"""WMS Adapter V1 API 导出。"""

from fastapi import APIRouter

from src.app.wms_adapter.v1.events import router as events_router

router = APIRouter()
router.include_router(events_router)

__all__ = ["router"]
