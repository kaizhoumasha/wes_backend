"""Transport V1 API。"""

from fastapi import APIRouter

from .evidence_stream import router as evidence_stream_router
from .tasks import router as tasks_router

router = APIRouter(prefix="/v1/transport")
router.include_router(tasks_router)
router.include_router(evidence_stream_router)

__all__ = ["router"]
