"""
角色相关模型

包含 Role 数据库表模型和相关的 Pydantic Schemas
"""

from datetime import datetime

from sqlalchemy.orm import relationship

from src.app.admin.models.perm import Permission, PermissionRead
from src.app.admin.models.relationships import role_permission
from src.core.mixins import BaseMixin, BaseTableModelMixin, Field

# ==================== Role 模型 ====================


class RoleBase(BaseMixin):
    """角色基础字段"""

    name: str = Field(max_length=100, unique=True, index=True)
    description: str | None = Field(default=None, max_length=255)
    is_active: bool = Field(default=True)


class Role(BaseTableModelMixin, RoleBase, table=True):  # type: ignore[misc]
    """角色表"""

    __tablename__ = "roles"


# ==================== Relationships ====================
# 在类外部定义关系（SQLModel 兼容方式）


Role.permissions = relationship(
    Permission,
    secondary=role_permission,
    back_populates="roles",
)


# ==================== Schemas ====================


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
    permissions: list[PermissionRead] = Field(default_factory=list)


# 别名：RoleResponse 与 RoleRead 完全相同，避免重复定义（DRY 原则）
RoleResponse = RoleRead  # type: ignore
