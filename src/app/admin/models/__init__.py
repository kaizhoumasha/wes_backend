"""
Admin 模型模块

导出所有 RBAC 相关的模型、Schema 和关联表
"""

from sqlalchemy.orm import relationship

# 导入所有模型
from .menu import (
    Menu,
    MenuBase,
    MenuCreate,
    MenuResponse,
    MenuTreeResponse,
    MenuUpdate,
)
from .perm import (
    Permission,
    PermissionBase,
    PermissionCreate,
    PermissionResponse,
    PermissionResponseSimple,
    PermissionTree,
    PermissionUpdate,
)
from .relationships import role_menu, role_permission, user_role
from .role import (
    Role,
    RoleBase,
    RoleCreate,
    RoleResponse,
    RoleResponseSimple,
    RoleUpdate,
)
from .user import (
    ResetPasswordRequest,
    User,
    UserBase,
    UserCreate,
    UserResponse,
    UserSimpleResponse,
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

# Menu-Role 多对多关系
Menu.roles = relationship(
    "Role",
    secondary=role_menu,
    back_populates="menus",
)
Role.menus = relationship(
    Menu,
    secondary=role_menu,
    back_populates="roles",
)

# ==================== 导出所有公开内容 ====================

__all__ = [
    # Menu 模型
    "Menu",
    "MenuBase",
    "MenuCreate",
    "MenuResponse",
    "MenuTreeResponse",
    "MenuUpdate",
    # Permission 模型
    "Permission",
    "PermissionBase",
    "PermissionCreate",
    "PermissionResponse",
    "PermissionResponseSimple",
    "PermissionTree",
    "PermissionUpdate",
    # User 模型
    "ResetPasswordRequest",
    # Role 模型
    "Role",
    "RoleBase",
    "RoleCreate",
    "RoleResponse",
    "RoleResponseSimple",
    "RoleUpdate",
    "User",
    "UserBase",
    "UserCreate",
    "UserResponse",
    "UserSimpleResponse",
    "UserUpdate",
    # 关联表
    "role_menu",
    "role_permission",
    "user_role",
]
