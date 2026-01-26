from sqlmodel import Field

from src.core.mixins import BaseMixin, BaseTableModelMixin, FullModelMixin
from src.database.model_factory import ModelFactory


class DemoProductBase(BaseMixin):
    """
    DemoProduct 基础字段
    """

    name: str = Field(max_length=100)
    price: float = Field(ge=0)
    stock: int = Field(ge=0)


class DemoProduct(DemoProductBase, BaseTableModelMixin, FullModelMixin, table=True):  # type: ignore[misc]
    """
    DemoProduct 模型
    """

    __tablename__ = "demo_products"  # type: ignore[misc]


DemoProductCreate = ModelFactory(DemoProductBase).for_create()
DemoProductUpdate = ModelFactory(DemoProductBase).for_update()


class DemoProductResponse(DemoProductBase):
    """
    DemoProduct 响应模型
    """

    id: int
