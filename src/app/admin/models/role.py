"""
角色相关模型

包含 Role 数据库表模型和相关的 Pydantic Schemas
"""

from typing import Literal

from sqlmodel import Field

from src.app.admin.models.perm import PermissionResponse
from src.core.mixins import BaseMixin, DataTableMixin
from src.database.model_factory import ModelFactory

# ==================== Role 模型 ====================


class RoleBase(BaseMixin):
    """角色基础字段"""

    name: str = Field(max_length=100, unique=True, index=True)
    description: str | None = Field(default=None, max_length=255)
    is_active: bool = Field(default=True)


class Role(RoleBase, DataTableMixin, table=True):  # type: ignore[misc]
    """角色表"""

    __tablename__: Literal["roles"] = "roles"


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
