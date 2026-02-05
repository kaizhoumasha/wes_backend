"""Permission Repository"""

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.admin.models.perm import Permission
from src.database.tree_repository import TreeRepository


class PermissionRepository(TreeRepository[Permission]):
    def __init__(self):
        super().__init__(Permission)

    def _add_deleted_filter(self, where_clauses: list, exclude_deleted: bool) -> None:
        """添加软删除过滤条件（DRY 原则）

        Args:
            where_clauses: WHERE 条件列表
            exclude_deleted: 是否排除已删除记录（True 表示只返回未删除的）
        """
        if exclude_deleted and hasattr(self.model, "is_deleted"):
            where_clauses.append(self.model.is_deleted.is_(False))  # type: ignore[arg-type]

    async def get_menu_tree(
        self,
        db: AsyncSession,
        include_deleted: bool = False,
    ) -> list[Permission]:
        """获取菜单树（type=menu，用于前端菜单）"""
        where_clauses = [Permission.type == "menu"]
        self._add_deleted_filter(where_clauses, not include_deleted)

        _, items = await self.get_list(
            db, limit=1000, where_clauses_raw=where_clauses, order_by_raw=[Permission.sort_order]
        )
        return items

    async def get_menu_permissions(
        self,
        db: AsyncSession,
        exclude_deleted: bool = True,
        include_hidden: bool = False,
    ) -> list[Permission]:
        """获取菜单权限（用于权限过滤）"""
        where_clauses = [Permission.type == "menu"]
        self._add_deleted_filter(where_clauses, exclude_deleted)
        if not include_hidden:
            where_clauses.append(Permission.is_hidden.is_(False))  # type: ignore[arg-type]

        _, items = await self.get_list(
            db, limit=1000, where_clauses_raw=where_clauses, order_by_raw=[Permission.sort_order]
        )
        return items

    async def get_api_permissions(
        self,
        db: AsyncSession,
        exclude_deleted: bool = True,
    ) -> list[Permission]:
        """获取 API 权限（type=user_api 或 type=external_api）"""
        where_clauses = [Permission.type.in_(["user_api", "external_api"])]
        self._add_deleted_filter(where_clauses, exclude_deleted)

        _, items = await self.get_list(db, limit=1000, where_clauses_raw=where_clauses)
        return items

    async def get_by_type(
        self,
        db: AsyncSession,
        perm_type: str,
        exclude_deleted: bool = True,
    ) -> list[Permission]:
        """按类型获取权限"""
        where_clauses = [Permission.type == perm_type]
        self._add_deleted_filter(where_clauses, exclude_deleted)

        _, items = await self.get_list(db, limit=1000, where_clauses_raw=where_clauses)
        return items

    async def get_by_name(self, db: AsyncSession, name: str) -> Permission | None:
        """按名称获取权限"""
        return await self.get_by_field(db, "name", name)


permission_repository = PermissionRepository()

__all__ = ["PermissionRepository", "permission_repository"]
