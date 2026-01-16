"""
SQLModel 数据模型

SQLModel 最佳实践：
- 合并数据库模型和 Pydantic schemas
- 使用 Mixin 类复用通用字段和行为
- 使用继承层次分离不同用途的类
"""
from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel

from src.core.mixins import BaseModelMixin


class UserBase(SQLModel):
    """用户基础字段 - 用于共享"""
    username: str = Field(min_length=3, max_length=50, index=True)
    email: str = Field(max_length=100, index=True)
    full_name: Optional[str] = Field(default=None, max_length=100)


class User(BaseModelMixin, UserBase, table=True):
    """
    用户数据库表模型

    继承 BaseModelMixin 获得时间戳字段和 repr 方法
    """
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    hashed_password: str = Field(max_length=255)
    is_active: bool = Field(default=True)


# Pydantic Schemas 用于 API 请求/响应
class UserCreate(UserBase):
    """用户创建 Schema - 接收客户端输入"""
    password: str = Field(min_length=6, max_length=100)


class UserUpdate(SQLModel):
    """用户更新 Schema - 所有字段可选"""
    email: Optional[str] = Field(default=None, max_length=100)
    full_name: Optional[str] = Field(default=None, max_length=100)
    is_active: Optional[bool] = None


class UserRead(UserBase):
    """用户响应 Schema - 返回给客户端"""
    id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class UserListResponse(SQLModel):
    """用户列表响应 Schema"""
    total: int
    items: list[UserRead]
