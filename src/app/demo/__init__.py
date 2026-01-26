from fastapi import APIRouter

from src.app.demo.v1.demo_product import router as demo_product_router

router_v1 = APIRouter(prefix="/v1")

router_v1.include_router(demo_product_router)

__ALL__ = ["router_v1"]
