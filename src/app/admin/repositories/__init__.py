"""Admin 模块 Repository"""

from .perm_repository import PermissionRepository, permission_repository
from .role_repository import RoleRepository, role_repository
from .user_repository import UserRepository, user_repository

__all__ = [
    "PermissionRepository",
    "RoleRepository",
    "UserRepository",
    "permission_repository",
    "role_repository",
    "user_repository",
]
