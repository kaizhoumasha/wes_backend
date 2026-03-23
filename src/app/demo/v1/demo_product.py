"""
DemoProduct API
"""

from src.app.demo.models.demo_product import DemoProduct, DemoProductCreate, DemoProductResponse, DemoProductUpdate
from src.app.demo.services.demo_product_service import demo_product_service
from src.core.base_api import BaseAPI

demo_product_api = BaseAPI(
    module_name="demo",
    model=DemoProduct,
    service=demo_product_service,
    create_schema=DemoProductCreate,
    update_schema=DemoProductUpdate,
    response_schema=DemoProductResponse,
    prefix="/demo-products",
    tags=["DemoProduct"],
    gen_create=True,
    gen_update=True,
    gen_delete=True,
    enable_permission=True,
    max_depth=2,
)

router = demo_product_api.router
