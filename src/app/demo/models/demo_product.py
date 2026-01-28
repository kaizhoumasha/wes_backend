from sqlmodel import Field, Relationship

from src.app.demo.models.demo_product_list import (
    DemoProductList,
    DemoProductListCreate,
    DemoProductListResponse,
    DemoProductListUpdate,
)
from src.core.mixins import BaseMixin, DataTableMixin, EnterpriseMixin, OptimisticLockMixin
from src.database.model_factory import ModelFactory


class DemoProductBase(BaseMixin):
    """
    DemoProduct 基础字段
    """

    name: str = Field(max_length=100)
    price: float = Field(ge=0)
    stock: int = Field(ge=0)


class DemoProduct(DemoProductBase, EnterpriseMixin, DataTableMixin, table=True):  # type: ignore[misc]
    """
    DemoProduct 模型

    使用 OptimisticLockMixin 实现乐观锁，防止并发修改冲突
    """

    __tablename__ = "demo_products"  # type: ignore[misc]

    product_lists: list[DemoProductList] = Relationship(back_populates="product")


class DemoProductCreate(ModelFactory(DemoProductBase).for_create()):
    """
    DemoProduct 创建模型
    """

    product_lists: list[DemoProductListCreate] = Field(default_factory=list)


class DemoProductUpdate(OptimisticLockMixin, ModelFactory(DemoProductBase).for_update()):
    """
    DemoProduct 更新模型

    注意：更新时必须包含 version 字段（乐观锁）
    """

    product_lists: list[DemoProductListUpdate] = Field(default_factory=list)
    version: int = Field(default=0)


class DemoProductResponse(OptimisticLockMixin, DemoProductBase):
    """
    DemoProduct 响应模型

    包含 version 字段，前端在更新时必须传回该字段
    """

    id: int
    product_lists: list[DemoProductListResponse]
