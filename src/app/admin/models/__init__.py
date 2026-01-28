"""
Admin 模型模块

导出所有 RBAC 相关的模型、Schema 和关联表
"""

from sqlalchemy.orm import relationship

# 导入所有模型
from .perm import (
    Permission,
    PermissionBase,
    PermissionCreate,
    PermissionResponse,
    PermissionResponseSimple,
    PermissionTree,
    PermissionUpdate,
)
from .relationships import role_permission, user_role
from .role import (
    Role,
    RoleBase,
    RoleCreate,
    RoleResponse,
    RoleResponseSimple,
    RoleUpdate,
)
from .user import (
    User,
    UserBase,
    UserCreate,
    UserResponse,
    UserUpdate,
)

# ==================== 处理循环引用 ====================
# User-Role 多对多关系需要在两个模型都定义后才能建立
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
    Permission,
    secondary=role_permission,
    back_populates="roles",
)
Permission.roles = relationship(
    "Role",
    secondary=role_permission,
    back_populates="permissions",
)

# ==================== 导出所有公开内容 ====================

__all__ = [
    # Permission 模型
    "Permission",
    "PermissionBase",
    "PermissionCreate",
    "PermissionResponse",
    "PermissionResponse",
    "PermissionResponseSimple",
    "PermissionTree",
    "PermissionUpdate",
    # Role 模型
    "Role",
    "RoleBase",
    "RoleCreate",
    "RoleResponse",
    "RoleResponse",
    "RoleResponseSimple",
    "RoleUpdate",
    # User 模型
    "User",
    "UserBase",
    "UserCreate",
    "UserResponse",
    "UserUpdate",
    "role_permission",
    # 关联表
    "user_role",
]
