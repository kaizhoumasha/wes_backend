from src.app.demo.models.demo_product import DemoProduct
from src.app.demo.repositories.demo_product_repository import DemoProductRepository, demo_product_repository
from src.core.base_service import BaseService


class DemoProductService(BaseService[DemoProduct, DemoProductRepository]):
    """
    DemoProduct 服务类
    """

    def __init__(self, repo: DemoProductRepository = demo_product_repository):
        super().__init__(repo, enable_cache=True)


demo_product_service = DemoProductService()

__all__ = ["demo_product_service"]
