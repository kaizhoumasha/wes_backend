"""
权限 Repository
"""

from src.app.admin.models import Permission
from src.database.base_repository import BaseRepository


class PermissionRepository(BaseRepository[Permission]):
    """权限 Repository"""

    def __init__(self):
        super().__init__(Permission)


permission_repository = PermissionRepository()
