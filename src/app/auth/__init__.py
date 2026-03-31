from fastapi import APIRouter

from src.app.auth.v1.auth import router as auth_router

router_v1 = APIRouter(prefix="/v1/auth")

router_v1.include_router(auth_router)

__ALL__ = ["router_v1"]
