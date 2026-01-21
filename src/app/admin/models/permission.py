"""
权限相关模型

包含 Permission 数据库表模型和相关的 Pydantic Schemas
"""

from datetime import datetime

from sqlalchemy.orm import relationship

from src.app.admin.models.relationships import role_permission
from src.core.mixins import BaseMixin, BaseTableModelMixin, Field

# ==================== Permission 模型 ====================


class PermissionBase(BaseMixin):
    """权限基础字段"""

    name: str = Field(max_length=100, unique=True, index=True)
    description: str | None = Field(default=None, max_length=255)


class Permission(BaseTableModelMixin, PermissionBase, table=True):
    """权限表"""

    __tablename__ = "permissions"


# ==================== Schemas ====================


class PermissionCreate(PermissionBase):
    """权限创建 Schema"""


class PermissionRead(PermissionBase):
    """权限响应 Schema"""

    id: int
    created_at: datetime
    updated_at: datetime | None = None


# ==================== Relationships ====================
# 在类外部定义关系（SQLModel 兼容方式）


Permission.roles = relationship(
    "Role",
    secondary=role_permission,
    back_populates="permissions",
)
