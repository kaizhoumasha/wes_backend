"""
Admin 模型模块

导出所有 RBAC 相关的模型、Schema 和关联表
"""

from sqlalchemy.orm import relationship

# 导入所有模型
from .permission import (
    Permission,
    PermissionBase,
    PermissionCreate,
    PermissionRead,
    PermissionReadSimple,
    PermissionResponse,
    PermissionTree,
    PermissionUpdate,
)
from .relationships import role_permission, user_role
from .role import (
    Role,
    RoleBase,
    RoleCreate,
    RoleRead,
    RoleReadSimple,
    RoleResponse,
    RoleUpdate,
)
from .user import (
    User,
    UserBase,
    UserCreate,
    UserListResponse,
    UserRead,
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

# ==================== 重建 Pydantic 模型 ====================
# 重建使用前向引用的 Pydantic 模型，解决类型注解问题
UserRead.model_rebuild()
PermissionTree.model_rebuild()

# ==================== 导出所有公开内容 ====================

__all__ = [
    # Permission 模型
    "Permission",
    "PermissionBase",
    "PermissionCreate",
    "PermissionRead",
    "PermissionReadSimple",
    "PermissionResponse",
    "PermissionTree",
    "PermissionUpdate",
    # Role 模型
    "Role",
    "RoleBase",
    "RoleCreate",
    "RoleRead",
    "RoleReadSimple",
    "RoleResponse",
    "RoleUpdate",
    # User 模型
    "User",
    "UserBase",
    "UserCreate",
    "UserListResponse",
    "UserRead",
    "UserUpdate",
    "role_permission",
    # 关联表
    "user_role",
]
