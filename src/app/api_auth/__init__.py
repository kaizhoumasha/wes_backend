from fastapi import APIRouter

from .v1.api_access_log import router as access_log_router
from .v1.api_application import router as application_router

router_v1 = APIRouter(prefix="/v1/api_auth")

# API v1 路由
router_v1.include_router(application_router)
router_v1.include_router(access_log_router)

__all__ = ["router_v1"]
