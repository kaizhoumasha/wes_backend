"""
权限管理 Service
"""

from src.app.admin.models import Permission
from src.app.admin.repositories.perm_repository import PermissionRepository, permission_repository
from src.core.base_service import BaseService


class PermissionService(BaseService[Permission, PermissionRepository]):
    """权限 Service"""

    def __init__(self, repo: PermissionRepository = permission_repository):
        super().__init__(repo, enable_cache=True, cache_prefix="permission:detail", cache_expire=3600)


permission_service = PermissionService()
