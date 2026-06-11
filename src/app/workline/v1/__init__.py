"""WorkLine V1 API 导出"""

from fastapi import APIRouter

from src.app.workline.services.debug_data_cleanup_service import NON_PROD_ENVS
from src.core.conf import settings

from .inbound_handoff import router as inbound_handoff_router
from .integration_debug import router as integration_debug_router
from .operation import router as operation_router
from .runtime import router as runtime_router
from .runtime_hold import router as runtime_hold_router
from .trace import router as trace_router
from .workline import router as workline_router

router = APIRouter()
router.include_router(workline_router)
router.include_router(runtime_hold_router)
router.include_router(operation_router, prefix="/operations")
router.include_router(trace_router, prefix="/trace")
router.include_router(trace_router, prefix="/traces", include_in_schema=False)
router.include_router(runtime_router, prefix="/runtime")
router.include_router(inbound_handoff_router, prefix="/inbound-handoff")
if settings.APP_ENV in NON_PROD_ENVS:
    router.include_router(integration_debug_router, prefix="/integration-debug")

__all__ = ["router"]
