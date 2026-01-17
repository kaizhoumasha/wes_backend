"""
SQLModel 数据模型

SQLModel 最佳实践：
- 合并数据库模型和 Pydantic schemas
- 使用 Mixin 类复用通用字段和行为
- 使用继承层次分离不同用途的类
"""

from datetime import datetime

from sqlalchemy import BigInteger, Column, ForeignKey, Table
from sqlalchemy.orm import relationship

from src.core.mixins import BaseMixin, BaseTableModelMixin, Field


class UserBase(BaseMixin):
    """用户基础字段 - 用于共享"""

    username: str = Field(min_length=3, max_length=50, index=True)
    email: str = Field(max_length=100, index=True)
    full_name: str | None = Field(default=None, max_length=100)


class User(BaseTableModelMixin, UserBase, table=True):
    """
    用户数据库表模型

    继承 BaseModelMixin 获得时间戳字段和 repr 方法
    """

    __tablename__ = "users"

    hashed_password: str = Field(max_length=255)
    is_active: bool = Field(default=True)
    is_superuser: bool = Field(default=False)
    is_multi_login: bool = Field(default=True)  # 是否允许多端登录


# ==================== RBAC 模型 ====================


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


class PermissionBase(BaseMixin):
    """权限基础字段"""

    name: str = Field(max_length=100, unique=True, index=True)  # 权限标识，如 "user:read"
    description: str | None = Field(default=None, max_length=255)


class Permission(BaseTableModelMixin, PermissionBase, table=True):
    """权限表"""

    __tablename__ = "permissions"


class RoleBase(BaseMixin):
    """角色基础字段"""

    name: str = Field(max_length=100, unique=True, index=True)  # 角色名称
    description: str | None = Field(default=None, max_length=255)
    is_active: bool = Field(default=True)  # 角色是否启用


class Role(BaseTableModelMixin, RoleBase, table=True):
    """角色表"""

    __tablename__ = "roles"


# 在类外部定义关系（SQLModel 兼容方式）
User.roles = relationship(
    "Role",
    secondary=user_role,
    back_populates="users",
)

Role.users = relationship(
    "User",
    secondary=user_role,
    back_populates="roles",
)

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


# ==================== Pydantic Schemas ====================


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
    roles: list["RoleRead"] = Field(default_factory=list)


class UserListResponse(BaseMixin):
    """用户列表响应 Schema"""

    total: int
    items: list[UserRead]


# ==================== 认证 Schemas ====================


class LoginRequest(BaseMixin):
    """登录请求 Schema"""

    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6, max_length=100)


class LoginResponse(BaseMixin):
    """登录响应 Schema"""

    access_token: str
    refresh_token: str
    access_token_expire_time: datetime
    refresh_token_expire_time: datetime
    session_uuid: str
    user: UserRead


class RefreshTokenResponse(BaseMixin):
    """刷新令牌响应 Schema"""

    access_token: str
    refresh_token: str
    access_token_expire_time: datetime
    refresh_token_expire_time: datetime
    session_uuid: str


# ==================== Role/Permission Schemas ====================


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
    permissions: list[PermissionRead] = Field(default_factory=list)
