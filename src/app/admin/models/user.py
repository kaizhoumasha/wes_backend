"""
用户相关模型

包含 User 数据库表模型和相关的 Pydantic Schemas
"""

from datetime import datetime

from src.app.admin.models.role import RoleRead
from src.core.mixins import BaseMixin, BaseTableModelMixin, Field


class UserBase(BaseMixin):
    """用户基础字段 - 用于共享"""

    username: str = Field(min_length=3, max_length=50, index=True)
    email: str = Field(max_length=100, index=True)
    full_name: str | None = Field(default=None, max_length=100)


class User(BaseTableModelMixin, UserBase, table=True):  # type: ignore[misc]
    """
    用户数据库表模型

    继承 BaseModelMixin 获得时间戳字段和 repr 方法
    """

    __tablename__ = "users"

    hashed_password: str = Field(max_length=255)
    is_active: bool = Field(default=True)
    is_superuser: bool = Field(default=False)
    is_multi_login: bool = Field(default=True)  # 是否允许多端登录


class UserCreate(UserBase):
    """用户创建 Schema - 接收客户端输入"""

    password: str = Field(min_length=6, max_length=100)


class UserUpdate(BaseMixin):
    """用户更新 Schema - 所有字段可选"""

    email: str | None = Field(default=None, max_length=100)
    full_name: str | None = Field(default=None, max_length=100)
    is_active: bool | None = None


class UserRead(UserBase):
    """用户响应 Schema - 返回给客户端"""

    id: int
    is_active: bool
    is_superuser: bool
    created_at: datetime
    updated_at: datetime | None = None
    roles: list[RoleRead] = Field(default_factory=list)  # 前向引用，将在 __init__.py 中重建


class UserListResponse(BaseMixin):
    """用户列表响应 Schema"""

    total: int
    items: list[UserRead]
