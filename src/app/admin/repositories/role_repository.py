"""角色 Repository"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.admin.models import user_role
from src.app.admin.models.role import Role
from src.database.base_repository import BaseRepository


class RoleRepository(BaseRepository[Role]):
    """角色 Repository"""

    def __init__(self):
        super().__init__(Role)

    async def get_user_ids_by_role_id(self, db: AsyncSession, role_id: int) -> set[int]:
        """获取角色关联的所有用户 ID

        Args:
            db: 数据库会话
            role_id: 角色 ID

        Returns:
            用户 ID 集合
        """
        result = await db.execute(select(user_role.c.user_id).where(user_role.c.role_id == role_id))
        return {int(user_id) for user_id in result.scalars().all() if user_id is not None}


# 单例实例
role_repository = RoleRepository()

__all__ = ["RoleRepository", "role_repository"]
