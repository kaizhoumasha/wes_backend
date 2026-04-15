from fastapi import APIRouter

from src.app.demo.v1.demo_product import router as demo_product_router

router_v1 = APIRouter(prefix="/v1/demo")

router_v1.include_router(demo_product_router)

__all__ = ["router_v1"]
