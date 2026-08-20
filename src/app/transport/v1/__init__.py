"""Transport V1 API。"""

from fastapi import APIRouter

from .tasks import router as tasks_router

router = APIRouter(prefix="/v1/transport")
router.include_router(tasks_router)

__all__ = ["router"]
