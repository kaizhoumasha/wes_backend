from sqlalchemy import Index
from sqlmodel import Field, Relationship

from src.app.demo.models.demo_product_list import (
    DemoProductList,
    DemoProductListCreate,
    DemoProductListResponse,
    DemoProductListUpdate,
)
from src.core.mixins import BaseMixin, DataTableMixin, EnterpriseMixin, OptimisticLockMixin, SoftDeleteMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType


class DemoProductBase(BaseMixin):
    """
    DemoProduct 基础字段
    """

    name: str = Field(
        max_length=100,
        sa_column_kwargs={
            "comment": "产品名称",
        },
    )
    price: float = Field(ge=0)
    stock: int = Field(ge=0)


class DemoProduct(DemoProductBase, EnterpriseMixin, SoftDeleteMixin, DataTableMixin, table=True):  # type: ignore[misc]
    """
    DemoProduct 模型

    使用 OptimisticLockMixin 实现乐观锁，防止并发修改冲突

    表级约束和索引：
    - name_active_unique: 部分唯一索引，只对未删除的记录强制 name 唯一性

    部分唯一索引支持情况：
    - PostgreSQL 8.4+: ✅ postgresql_where="NOT is_deleted"
    - SQLite 3.8.0+: ✅ sqlite_where="NOT is_deleted"
    - SQL Server 2008+: ✅ mssql_where="NOT is_deleted"
    - MySQL 8.0+: ⚠️ 需要使用函数索引（语法较复杂）
    - MySQL < 8.0: ❌ 不支持，建议使用 PostgreSQL
    """

    __tablename__ = "demo_products"  # type: ignore[misc]
    __schema__ = SchemaType.BIZ.value  # 业务表

    # 定义部分唯一索引：只对未删除的记录强制 name 唯一性
    # 这解决了软删除与唯一约束冲突的问题
    __table_args__ = (
        Index(
            "demo_products_name_active_unique",  # 索引名称
            "name",  # 索引列
            unique=True,  # 唯一索引
            # PostgreSQL 语法（推荐）
            postgresql_where="NOT is_deleted",
            # SQLite 语法
            # sqlite_where="NOT is_deleted",
            # SQL Server 语法
            # mssql_where="NOT is_deleted",
        ),
    )

    product_lists: list[DemoProductList] = Relationship(
        back_populates="product",
        passive_deletes=True,  # 依赖数据库 ON DELETE CASCADE
    )


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


class DemoProductResponse(DemoProductBase, EnterpriseMixin, SoftDeleteMixin):
    """
    DemoProduct 响应模型

    包含 version 字段，前端在更新时必须传回该字段
    """

    id: int
    product_lists: list[DemoProductListResponse]
