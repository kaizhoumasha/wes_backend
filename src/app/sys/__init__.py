from fastapi import APIRouter

from src.app.sys.v1.audit_log import router as audit_log_router
from src.app.sys.v1.events import router as events_router

router_v1 = APIRouter(prefix="/v1/sys")

router_v1.include_router(audit_log_router)
router_v1.include_router(events_router)

__ALL__ = ["router_v1"]
