from src.app.demo.models.demo_product import DemoProduct
from src.database.base_repository import BaseRepository


class DemoProductRepository(BaseRepository[DemoProduct]):
    def __init__(self):
        super().__init__(DemoProduct)


demo_product_repository = DemoProductRepository()

__all__ = ["demo_product_repository"]
