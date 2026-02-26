"""Admin 模块 Repository"""

from .menu_repository import MenuRepository, menu_repository
from .perm_repository import PermissionRepository, permission_repository
from .role_repository import RoleRepository, role_repository
from .user_repository import UserRepository, user_repository

__all__ = [
    "MenuRepository",
    "PermissionRepository",
    "RoleRepository",
    "UserRepository",
    "menu_repository",
    "permission_repository",
    "role_repository",
    "user_repository",
]
