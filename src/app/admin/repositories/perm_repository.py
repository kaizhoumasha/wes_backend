"""API 权限 Repository"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.admin.models import Permission, Role, User, role_permission, user_role
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

    async def get_permission_names_by_user_id(self, db: AsyncSession, user_id: int) -> set[str]:
        """获取用户的所有权限标识

        Args:
            db: 数据库会话
            user_id: 用户 ID

        Returns:
            权限标识集合，超级用户返回 {"*"}，用户不存在时返回空集合

        优化说明:
            - 使用 is_deleted 替代 is_active 简化状态管理
            - is_deleted=False 表示角色/权限启用
            - is_deleted=True 表示角色/权限已禁用/删除
        """
        # 查询用户（预加载 roles 和 permissions）
        result = await db.execute(
            select(User).where(User.id == user_id).options(selectinload(User.roles).selectinload(Role.permissions))
        )
        user = result.scalar_one_or_none()

        if not user:
            return set()

        # 超级用户返回特殊标识
        if user.is_superuser:
            return {"*"}

        # 收集权限标识
        permissions = set()
        for role in user.roles:
            # 只收集未删除的角色权限
            if not role.is_deleted:
                for perm in role.permissions:
                    # 只收集未删除的权限
                    if not perm.is_deleted:
                        permissions.add(perm.name)
        return permissions

    async def get_user_ids_by_permission_id(self, db: AsyncSession, permission_id: int) -> set[int]:
        """获取权限关联的所有用户 ID

        Args:
            db: 数据库会话
            permission_id: 权限 ID

        Returns:
            用户 ID 集合
        """
        query = (
            select(user_role.c.user_id)
            .join(role_permission, role_permission.c.role_id == user_role.c.role_id)
            .where(role_permission.c.permission_id == permission_id)
            .distinct()
        )
        result = await db.execute(query)
        return {int(user_id) for user_id in result.scalars().all() if user_id is not None}

    async def get_permission_names_by_app_id(self, db: AsyncSession, app_id: int) -> set[str]:
        """获取应用拥有的权限名称集合

        Args:
            db: 数据库会话
            app_id: 应用 ID

        Returns:
            权限名称集合

        设计原则:
            - SRP: 数据访问逻辑应在 Repository 层，不应在 Service 层
            - DRY: 复用 _add_deleted_filter 方法进行软删除过滤
            - 延迟导入: 避免循环导入问题
            - 行为保持: 与原有实现保持相同的查询结构，确保测试兼容性
        """
        # 延迟导入避免循环导入
        from src.app.api_auth.models.relationships import api_app_permissions

        where_clauses: list = [
            api_app_permissions.c.app_id == app_id,
        ]

        # 添加软删除过滤
        self._add_deleted_filter(where_clauses, exclude_deleted=True)

        # 构建查询（保持与原有实现相同的结构）
        query = (
            select(Permission)
            .join(api_app_permissions, api_app_permissions.c.permission_id == Permission.id)
            .where(*where_clauses)
        )

        result = await db.execute(query)
        return {row.name for row in result.scalars()}

    async def get_app_ids_by_permission_id(self, db: AsyncSession, permission_id: int) -> set[int]:
        """根据权限 ID 查询使用该权限的应用 ID 集合

        Args:
            db: 数据库会话
            permission_id: 权限 ID

        Returns:
            应用 ID 集合

        设计原则:
            - SRP: 数据访问逻辑应在 Repository 层，不应在 Service 层
            - 延迟导入: 避免循环导入问题
            - DRY: 与 get_user_ids_by_permission_id 保持一致的查询模式
        """
        # 延迟导入避免循环导入
        from src.app.api_auth.models.relationships import api_app_permissions

        query = (
            select(api_app_permissions.c.app_id)
            .where(api_app_permissions.c.permission_id == permission_id)
            .distinct()
        )
        result = await db.execute(query)
        return {int(app_id) for app_id in result.scalars().all() if app_id is not None}


permission_repository = PermissionRepository()

__all__ = ["PermissionRepository", "permission_repository"]
