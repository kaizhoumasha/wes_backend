"""角色 Repository"""

from src.app.admin.models.role import Role
from src.database.base_repository import BaseRepository


class RoleRepository(BaseRepository[Role]):
    """角色 Repository"""

    def __init__(self):
        super().__init__(Role)


# 单例实例
role_repository = RoleRepository()

__all__ = ["RoleRepository", "role_repository"]
