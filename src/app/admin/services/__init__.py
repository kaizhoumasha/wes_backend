"""
服务层模块

包含业务逻辑服务，分离路由和业务逻辑

Note: 密码哈希工具已移至 src.utils.password_hasher
"""

from .authorization_bootstrap_service import (
    BUILTIN_ROLE_SPECS,
    AuthorizationBootstrapService,
    AuthorizationCacheInvalidationError,
    AuthorizationSyncResult,
    BootstrapFoundationConfig,
    FoundationBootstrapResult,
    authorization_bootstrap_service,
)
from .menu_service import MenuService, menu_service
from .menu_sync_service import MenuSyncResult, MenuSyncService, menu_sync_service
from .perm_service import PermissionService, permission_service
from .permission_catalog_service import (
    PermissionCatalogService,
    PermissionCatalogSyncResult,
    permission_catalog_service,
)
from .role_service import RoleService, role_service
from .user_service import UserService, user_service

__all__ = [
    "BUILTIN_ROLE_SPECS",
    "AuthorizationBootstrapService",
    "AuthorizationCacheInvalidationError",
    "AuthorizationSyncResult",
    "BootstrapFoundationConfig",
    "FoundationBootstrapResult",
    "MenuService",
    "MenuSyncResult",
    "MenuSyncService",
    "PermissionCatalogService",
    "PermissionCatalogSyncResult",
    "PermissionService",
    "RoleService",
    "UserService",
    "authorization_bootstrap_service",
    "menu_service",
    "menu_sync_service",
    "permission_catalog_service",
    "permission_service",
    "role_service",
    "user_service",
]
