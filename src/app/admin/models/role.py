"""
角色和权限相关模型

包含 Role、Permission 数据库表模型、关联表和相关的 Pydantic Schemas
"""

from datetime import datetime

from sqlalchemy import BigInteger, Column, ForeignKey, Table
from sqlalchemy.orm import relationship

from src.core.mixins import BaseMixin, BaseTableModelMixin, Field

# ==================== 关联表定义 ====================


# User-Role 多对多关联表
# 注意：外键列使用 BigInteger 以匹配主键类型
user_role = Table(
    "user_roles",
    BaseTableModelMixin.metadata,
    Column(
        "user_id",
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "role_id",
        BigInteger,
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    comment="用户-角色关联表",
)


# Role-Permission 多对多关联表
# 注意：外键列使用 BigInteger 以匹配主键类型
role_permission = Table(
    "role_permissions",
    BaseTableModelMixin.metadata,
    Column(
        "role_id",
        BigInteger,
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "permission_id",
        BigInteger,
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    comment="角色-权限关联表",
)


# ==================== Permission 模型 ====================


class PermissionBase(BaseMixin):
    """权限基础字段"""

    name: str = Field(max_length=100, unique=True, index=True)
    description: str | None = Field(default=None, max_length=255)


class Permission(BaseTableModelMixin, PermissionBase, table=True):
    """权限表"""

    __tablename__ = "permissions"


# ==================== Role 模型 ====================


class RoleBase(BaseMixin):
    """角色基础字段"""

    name: str = Field(max_length=100, unique=True, index=True)
    description: str | None = Field(default=None, max_length=255)
    is_active: bool = Field(default=True)


class Role(BaseTableModelMixin, RoleBase, table=True):
    """角色表"""

    __tablename__ = "roles"


# ==================== Relationships ====================
# 在类外部定义关系（SQLModel 兼容方式）


Role.permissions = relationship(
    "Permission",
    secondary=role_permission,
    back_populates="roles",
)

Permission.roles = relationship(
    "Role",
    secondary=role_permission,
    back_populates="permissions",
)


# ==================== Schemas ====================


class PermissionCreate(PermissionBase):
    """权限创建 Schema"""


class PermissionRead(PermissionBase):
    """权限响应 Schema"""

    id: int
    created_at: datetime
    updated_at: datetime | None = None


class RoleCreate(RoleBase):
    """角色创建 Schema"""

    permission_ids: list[int] | None = Field(default=None)


class RoleUpdate(BaseMixin):
    """角色更新 Schema"""

    name: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None
    permission_ids: list[int] | None = None


class RoleReadSimple(RoleBase):
    """角色响应 Schema（简化版，不含权限）"""

    id: int
    created_at: datetime
    updated_at: datetime | None = None


class RoleRead(RoleBase):
    """角色响应 Schema"""

    id: int
    created_at: datetime
    updated_at: datetime | None = None
    permissions: list[PermissionRead] = Field(default_factory=list)  # 同文件引用，无需前向引用


class RoleResponse(RoleBase):
    """角色响应 Schema"""

    id: int
    created_at: datetime
    updated_at: datetime | None = None
    permissions: list["PermissionRead"] = Field(default_factory=list)
