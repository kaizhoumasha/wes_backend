"""
角色相关模型

包含 Role 数据库表模型和相关的 Pydantic Schemas
"""

from typing import Literal

from sqlmodel import Field, Index

from src.app.admin.models.perm import PermissionResponse
from src.core.mixins import BaseMixin, DataTableMixin, EnterpriseMixin, SoftDeleteMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType

# ==================== Role 模型 ====================


class RoleBase(BaseMixin):
    """角色基础字段"""

    name: str = Field(max_length=100, index=True)
    description: str | None = Field(default=None, max_length=255)


class Role(RoleBase, EnterpriseMixin, SoftDeleteMixin, DataTableMixin, table=True):  # type: ignore[misc]
    """角色表"""

    __tablename__: Literal["roles"] = "roles"
    __schema__ = SchemaType.SYS.value  # 系统管理表

    __table_args__ = (
        # 角色名称唯一索引（软删除后可重用名称）
        Index(
            "ux_roles_name_deleted",  # 索引名称
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


# ==================== Schemas ====================


class RoleCreate(ModelFactory(RoleBase).for_create()):
    """角色创建 Schema"""


class RoleUpdate(ModelFactory(RoleBase).for_update()):
    """角色更新 Schema"""


class RoleResponseSimple(RoleBase):
    """角色响应 Schema（简化版，不含权限）"""

    id: int


class RoleResponse(RoleBase):
    """角色响应 Schema"""

    id: int
    permissions: list[PermissionResponse] = Field(default_factory=list)
