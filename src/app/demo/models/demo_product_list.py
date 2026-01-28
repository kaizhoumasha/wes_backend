from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Column, ForeignKey
from sqlmodel import Field, Relationship

from src.core.mixins import BaseMixin, DataTableMixin
from src.database.model_factory import ModelFactory

if TYPE_CHECKING:
    from src.app.demo.models.demo_product import DemoProduct


class DemoProductListBase(BaseMixin):
    """
    DemoProduct 基础字段
    """

    product_id: int = Field(sa_column=Column(BigInteger, ForeignKey("demo_products.id", ondelete="CASCADE")))
    quantity: int = Field(ge=0)


class DemoProductList(DemoProductListBase, DataTableMixin, table=True):  # type: ignore[misc]
    """
    DemoProductList 模型（子表）

    注意：子表不继承 EnterpriseMixin，只保留基础字段：
    - id（主键）
    - created_at, updated_at（时间戳）

    不包含审计和软删除字段，因为：
    - 审计信息由主表 DemoProduct 记录
    - 删除操作由主表控制，不需要独立软删除
    """

    __tablename__ = "demo_product_lists"  # type: ignore[misc]

    product: "DemoProduct" = Relationship(back_populates="product_lists")


_DemoProductListCreate = ModelFactory(DemoProductListBase).for_create()


class DemoProductListCreate(_DemoProductListCreate):
    """
    DemoProductList 创建模型

    注意：product_id 在创建时是可选的，因为会自动从主表 ID 设置
    """

    product_id: int | None = None


_DemoProductListUpdate = ModelFactory(DemoProductListBase).for_update()


class DemoProductListUpdate(_DemoProductListUpdate):
    """
    DemoProductList 更新模型

    注意：在更新主表时，使用 Diff 算法处理从表：
    - 有 id：更新现有记录
    - 无 id：创建新记录
    - 缺失：删除记录

    因此 id 和 product_id 都是可选的
    """

    id: int | None = None
    product_id: int | None = None


class DemoProductListResponse(DemoProductListBase):
    """
    DemoProductList 响应模型
    """

    id: int
