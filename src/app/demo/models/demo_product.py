from sqlmodel import Field, Relationship

from src.app.demo.models.demo_product_list import (
    DemoProductList,
    DemoProductListCreate,
    DemoProductListResponse,
    DemoProductListUpdate,
)
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

    product_lists: list[DemoProductList] = Relationship(back_populates="product")


_DemoProductCreate = ModelFactory(DemoProductBase).for_create()


class DemoProductCreate(_DemoProductCreate):
    """
    DemoProduct 创建模型
    """

    product_lists: list[DemoProductListCreate] = Field(default_factory=list)


_DemoProductUpdate = ModelFactory(DemoProductBase).for_update()


class DemoProductUpdate(_DemoProductUpdate):
    """
    DemoProduct 更新模型
    """

    product_lists: list[DemoProductListUpdate] = Field(default_factory=list)


class DemoProductResponse(DemoProductBase):
    """
    DemoProduct 响应模型
    """

    id: int
    product_lists: list[DemoProductListResponse]
