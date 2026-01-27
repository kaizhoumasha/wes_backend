"""
角色 Service
"""

from src.app.admin.models import Role
from src.app.admin.repositories.role_repository import role_repository
from src.core.base_service import BaseService
from src.core.cache_config import cache_settings
from src.database.base_repository import BaseRepository


class RoleService(BaseService[Role, BaseRepository]):
    """角色 Service"""

    def __init__(self, repo: BaseRepository = role_repository):
        super().__init__(
            repo,
            enable_cache=True,
            cache_prefix=cache_settings.ROLE.prefix,
            cache_expire=cache_settings.ROLE.expire,
        )


role_service = RoleService()
