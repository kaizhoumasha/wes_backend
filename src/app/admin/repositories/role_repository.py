"""
角色 Repository
"""

from src.app.admin.models import Role
from src.database.base_repository import BaseRepository


class RoleRepository(BaseRepository[Role]):
    """角色 Repository"""

    def __init__(self):
        super().__init__(Role)


role_repository = RoleRepository()
