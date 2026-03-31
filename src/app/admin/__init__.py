from fastapi import APIRouter

from src.app.admin.v1.menu import router as menu_router
from src.app.admin.v1.performance import router as performance_router
from src.app.admin.v1.perm import router as perm_router
from src.app.admin.v1.role import router as role_router
from src.app.admin.v1.user import router as user_router

router_v1 = APIRouter(prefix="/v1/admin")

# API v1 路由
router_v1.include_router(user_router)
router_v1.include_router(role_router)
router_v1.include_router(perm_router)
router_v1.include_router(menu_router)
router_v1.include_router(performance_router)

__ALL__ = ["router_v1"]
