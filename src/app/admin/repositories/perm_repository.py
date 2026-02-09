"""API 权限 Repository"""

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.admin.models.perm import Permission
from src.database.tree_repository import TreeRepository


class PermissionRepository(TreeRepository[Permission]):
    """API 权限 Repository"""

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

    async def get_api_permissions(
        self,
        db: AsyncSession,
        perm_type: str | None = None,
        exclude_deleted: bool = True,
    ) -> list[Permission]:
        """获取 API 权限

        Args:
            db: 数据库会话
            perm_type: 权限类型过滤（user_api/app_api），None 表示获取所有
            exclude_deleted: 是否排除已删除的权限

        Returns:
            API 权限列表
        """
        where_clauses: list = []

        # 类型过滤
        if perm_type:
            where_clauses.append(Permission.type == perm_type)
        else:
            # 默认获取所有 API 类型
            where_clauses.append(Permission.type.in_(["user_api", "app_api"]))

        # 软删除过滤
        self._add_deleted_filter(where_clauses, exclude_deleted)

        _, items = await self.get_list(db, limit=1000, where_clauses_raw=where_clauses)
        return items

    async def get_by_type(
        self,
        db: AsyncSession,
        perm_type: str,
        exclude_deleted: bool = True,
    ) -> list[Permission]:
        """按类型获取权限

        Args:
            db: 数据库会话
            perm_type: 权限类型（user_api/app_api）
            exclude_deleted: 是否排除已删除的权限

        Returns:
            权限列表
        """
        where_clauses = [Permission.type == perm_type]
        self._add_deleted_filter(where_clauses, exclude_deleted)

        _, items = await self.get_list(db, limit=1000, where_clauses_raw=where_clauses)
        return items

    async def get_by_name(self, db: AsyncSession, name: str) -> Permission | None:
        """按名称获取权限

        Args:
            db: 数据库会话
            name: 权限名称

        Returns:
            权限对象或 None
        """
        return await self.get_by_field(db, "name", name)


permission_repository = PermissionRepository()

__all__ = ["PermissionRepository", "permission_repository"]
