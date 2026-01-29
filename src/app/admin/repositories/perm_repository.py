"""Permission Repository"""

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.admin.models.perm import Permission
from src.database.tree_repository import TreeRepository


class PermissionRepository(TreeRepository[Permission]):
    def __init__(self):
        super().__init__(Permission)

    def _add_active_filter(self, where_clauses: list, active_only: bool) -> None:
        """添加活跃状态过滤条件（DRY 原则）"""
        if active_only and hasattr(self.model, "is_active"):
            where_clauses.append(self.model.is_active)

    async def get_menu_tree(
        self,
        db: AsyncSession,
        include_inactive: bool = False,
    ) -> list[Permission]:
        """获取菜单树（type=menu，用于前端菜单）"""
        where_clauses = [Permission.type == "menu"]
        self._add_active_filter(where_clauses, not include_inactive)

        _, items = await self.get_list(
            db, limit=1000, where_clauses_raw=where_clauses, order_by_raw=[Permission.sort_order]
        )
        return items

    async def get_menu_permissions(
        self,
        db: AsyncSession,
        active_only: bool = True,
        include_hidden: bool = False,
    ) -> list[Permission]:
        """获取菜单权限（用于权限过滤）"""
        where_clauses = [Permission.type == "menu"]
        self._add_active_filter(where_clauses, active_only)
        if not include_hidden:
            where_clauses.append(Permission.is_hidden.is_(False))  # type: ignore[arg-type]

        _, items = await self.get_list(
            db, limit=1000, where_clauses_raw=where_clauses, order_by_raw=[Permission.sort_order]
        )
        return items

    async def get_api_permissions(
        self,
        db: AsyncSession,
        active_only: bool = True,
    ) -> list[Permission]:
        """获取 API 权限（type=api）"""
        where_clauses = [Permission.type == "api"]
        self._add_active_filter(where_clauses, active_only)

        _, items = await self.get_list(db, limit=1000, where_clauses_raw=where_clauses)
        return items

    async def get_by_type(
        self,
        db: AsyncSession,
        perm_type: str,
        active_only: bool = False,
    ) -> list[Permission]:
        """按类型获取权限"""
        where_clauses = [Permission.type == perm_type]
        self._add_active_filter(where_clauses, active_only)

        _, items = await self.get_list(db, limit=1000, where_clauses_raw=where_clauses)
        return items

    async def get_by_name(self, db: AsyncSession, name: str) -> Permission | None:
        """按名称获取权限"""
        return await self.get_by_field(db, "name", name)


permission_repository = PermissionRepository()

__all__ = ["PermissionRepository", "permission_repository"]
