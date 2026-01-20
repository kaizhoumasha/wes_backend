"""
角色 Service
"""

from src.app.admin.models import Role
from src.app.admin.repositories.role_repository import RoleRepository, role_repository
from src.core.base_service import BaseService


class RoleService(BaseService[Role, RoleRepository]):
    """角色 Service"""

    def __init__(self, repo: RoleRepository = role_repository):
        super().__init__(repo, enable_cache=True, cache_prefix="role:detail", cache_expire=3600)


role_service = RoleService()
