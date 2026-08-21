"""角色 Repository"""

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.admin.models import role_permission, user_role
from src.app.admin.models.role import Role
from src.database.base_repository import BaseRepository


class RoleRepository(BaseRepository[Role]):
    """角色 Repository"""

    def __init__(self):
        super().__init__(Role)

    async def get_active_by_names(self, db: AsyncSession, names: set[str]) -> dict[str, Role]:
        """按名称读取未删除角色。"""
        if not names:
            return {}
        result = await db.execute(select(Role).where(Role.name.in_(names), Role.is_deleted.is_(False)))
        return {role.name: role for role in result.scalars().all()}

    async def get_permission_ids_by_role_ids(
        self,
        db: AsyncSession,
        role_ids: set[int],
    ) -> dict[int, set[int]]:
        """批量读取角色当前权限 ID。"""
        permissions_by_role = {role_id: set() for role_id in role_ids}
        if not role_ids:
            return permissions_by_role
        result = await db.execute(
            select(role_permission.c.role_id, role_permission.c.permission_id).where(
                role_permission.c.role_id.in_(role_ids)
            )
        )
        for role_id, permission_id in result.all():
            permissions_by_role[int(role_id)].add(int(permission_id))
        return permissions_by_role

    async def apply_permission_delta(
        self,
        db: AsyncSession,
        role_id: int,
        added_permission_ids: set[int],
        removed_permission_ids: set[int],
    ) -> None:
        """精确应用一个角色的权限关联差量并 flush。"""
        if added_permission_ids:
            await db.execute(
                insert(role_permission),
                [
                    {"role_id": role_id, "permission_id": permission_id}
                    for permission_id in sorted(added_permission_ids)
                ],
            )
        if removed_permission_ids:
            await db.execute(
                delete(role_permission).where(
                    role_permission.c.role_id == role_id,
                    role_permission.c.permission_id.in_(removed_permission_ids),
                )
            )
        await db.flush()

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

    async def get_by_ids(self, db: AsyncSession, role_ids: list[int]) -> list[Role]:
        """批量获取角色

        Args:
            db: 数据库会话
            role_ids: 角色 ID 列表

        Returns:
            角色列表
        """
        if not role_ids:
            return []
        # 使用足够大的 limit 确保返回所有请求的角色
        _, roles = await self.get_list(
            db,
            limit=len(role_ids),
            where_clauses_raw=[Role.id.in_(role_ids)],  # type: ignore[attr-defined]
            include_deleted=True,
        )  # type: ignore[attr-defined]
        return roles


# 单例实例
role_repository = RoleRepository()

__all__ = ["RoleRepository", "role_repository"]
