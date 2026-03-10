"""
用户相关模型

包含 User 数据库表模型和相关的 Pydantic Schemas
"""

from typing import Literal

from sqlmodel import Field

from src.app.admin.models.role import RoleResponse
from src.core.mixins import BaseMixin, DataTableMixin, EnterpriseMixin, SoftDeleteMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType


class UserBase(BaseMixin):
    """用户基础字段 - 用于共享"""

    username: str = Field(min_length=3, max_length=50, index=True)
    email: str = Field(max_length=100, index=True)
    full_name: str | None = Field(default=None, max_length=100)


class User(UserBase, EnterpriseMixin, SoftDeleteMixin, DataTableMixin, table=True):
    """
    用户数据库表模型

    继承 StandardMixin 获得时间戳字段和 repr 方法
    """

    __tablename__: Literal["users"] = "users"
    __schema__ = SchemaType.SYS.value  # 系统管理表

    hashed_password: str = Field(max_length=255)
    is_superuser: bool = Field(default=False)
    is_multi_login: bool = Field(default=False)  # 是否允许多端登录


class UserCreate(ModelFactory(UserBase).for_create()):
    """用户创建 Schema - 接收客户端输入"""

    password: str = Field(min_length=6, max_length=100)


class UserUpdate(ModelFactory(UserBase).for_optimistic_update()):
    """用户更新 Schema - 所有字段可选"""


class UserResponse(UserBase):
    """用户响应 Schema - 返回给客户端"""

    id: int
    version: int
    is_superuser: bool
    is_multi_login: bool
    roles: list[RoleResponse] = Field(default_factory=list)
