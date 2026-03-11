"""
用户相关模型

包含 User 数据库表模型和相关的 Pydantic Schemas
"""

from datetime import datetime
from typing import Literal

from pydantic import EmailStr, field_validator
from sqlalchemy import Index, text
from sqlmodel import Field

from src.app.admin.models.role import RoleResponse
from src.core.mixins import BaseMixin, DataTableMixin, EnterpriseMixin, SoftDeleteMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType


class UserBase(BaseMixin):
    """用户基础字段 - 用于共享"""

    username: str = Field(min_length=3, max_length=50, index=True, description="用户名")
    email: EmailStr = Field(max_length=100, index=True, description="邮箱")
    full_name: str | None = Field(default=None, max_length=100, description="姓名")

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value: str | None) -> str | None:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("full_name", mode="before")
    @classmethod
    def normalize_full_name(cls, value: str | None) -> str | None:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


class User(UserBase, EnterpriseMixin, SoftDeleteMixin, DataTableMixin, table=True):
    """
    用户数据库表模型

    继承 StandardMixin 获得时间戳字段和 repr 方法
    """

    __tablename__: Literal["users"] = "users"
    __schema__ = SchemaType.SYS.value  # 系统管理表
    __table_args__ = (
        Index(
            "ux_users_username_deleted",
            "username",
            unique=True,
            postgresql_where=text("NOT is_deleted"),
            sqlite_where=text("NOT is_deleted"),
        ),
        Index(
            "ux_users_email_deleted",
            "email",
            unique=True,
            postgresql_where=text("NOT is_deleted"),
            sqlite_where=text("NOT is_deleted"),
        ),
    )

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
    version: int = 0  # OptimisticLockMixin 提供，必需字段
    is_superuser: bool
    is_multi_login: bool
    created_at: datetime
    updated_at: datetime | None
    roles: list[RoleResponse] = Field(default_factory=list)
