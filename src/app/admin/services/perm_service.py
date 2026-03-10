"""
API 权限管理 Service

提供 API 权限相关的业务逻辑：
- 权限缓存管理
- 用户权限收集
- API 权限查询
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.admin.models import Permission, role_permission, user_role
from src.app.admin.repositories.perm_repository import PermissionRepository, permission_repository
from src.common.cache_config import cache_settings
from src.core.base_service import BaseService
from src.core.rbac import invalidate_users_permissions
from src.core.tree_service import TreeServiceMixin
from src.database.base_repository import HookContext, HookType
from src.database.redis_cache import get_cache

# ==================== 常量 ====================

SUPERUSER_PERMISSION = "*"  # 超级用户权限标识


class PermissionService(TreeServiceMixin[Permission], BaseService[Permission, PermissionRepository]):
    """API 权限 Service（支持树形结构）"""

    def __init__(self, repo: PermissionRepository = permission_repository):
        # 初始化 TreeServiceMixin（设置 self.repo）
        TreeServiceMixin.__init__(self, repo)
        # 初始化 BaseService（缓存配置）
        BaseService.__init__(
            self,
            repo,
            enable_cache=True,
            cache_prefix=cache_settings.PERMISSION.prefix,
            cache_expire=cache_settings.PERMISSION.expire,
            list_cache_prefix=cache_settings.PERMISSION_LIST.prefix,
            list_cache_expire=cache_settings.PERMISSION_LIST.expire,
        )
        self.repo: PermissionRepository = repo
        self.add_hook(HookType.BEFORE_UPDATE, self._before_permission_update_capture_user_ids, priority=100)
        self.add_hook(HookType.AFTER_CREATE, self._after_permission_change_invalidate_user_permissions, priority=100)
        self.add_hook(HookType.AFTER_UPDATE, self._after_permission_change_invalidate_user_permissions, priority=100)
        self.add_hook(HookType.AFTER_DELETE, self._after_permission_change_invalidate_user_permissions, priority=100)
        self.add_hook(HookType.BEFORE_UPDATE, self._before_permission_update_capture_app_ids, priority=100)
        self.add_hook(HookType.AFTER_UPDATE, self._after_permission_change_invalidate_app_permissions, priority=100)
        self.add_hook(HookType.AFTER_DELETE, self._after_permission_change_invalidate_app_permissions, priority=100)

    # ==================== 用户权限收集 ====================

    async def get_user_permissions(self, db: AsyncSession, user_id: int) -> set[str]:
        """获取用户的所有 API 权限

        Args:
            db: 数据库会话
            user_id: 用户 ID

        Returns:
            权限标识集合，超级用户返回 {SUPERUSER_PERMISSION}
            用户不存在时返回空集合

        说明:
            - 委托给 Repository 层处理数据访问
            - Service 层只负责业务逻辑协调
        """
        return await self.repo.get_permission_names_by_user_id(db, user_id)

    async def get_api_permissions(
        self,
        db: AsyncSession,
        perm_type: str | None = None,
        exclude_deleted: bool = True,
    ) -> list[Permission]:
        """获取 API 权限列表

        Args:
            db: 数据库会话
            perm_type: 权限类型过滤（user_api/app_api），None 表示获取所有
            exclude_deleted: 是否排除已删除的权限

        Returns:
            API 权限列表
        """
        return await self.repo.get_api_permissions(db, perm_type=perm_type, exclude_deleted=exclude_deleted)

    async def get_user_api_permissions(
        self,
        db: AsyncSession,
        user_id: int,
        perm_type: str = "user_api",
    ) -> list[Permission]:
        """获取用户的 API 权限列表（用于前端动态路由）

        Args:
            db: 数据库会话
            user_id: 用户 ID
            perm_type: 权限类型过滤（默认 user_api，前端只需要内部管理 API）

        Returns:
            用户有权限访问的 API 权限列表（包含 method、path 等信息）

        使用场景：
            - 前端登录后获取用户可访问的 API 列表
            - 前端根据 API 权限动态显示/隐藏功能按钮
            - 前端根据 API 权限控制路由访问

        说明：
            - user_api: 内部管理 API（前端管理系统使用）
            - app_api: 外部应用 API（第三方应用集成，前端不需要）
        """
        # 获取用户的所有权限标识
        permission_names = await self.get_user_permissions(db, user_id)

        # 超级用户返回所有 API 权限
        if SUPERUSER_PERMISSION in permission_names:
            return await self.get_api_permissions(db, perm_type=perm_type, exclude_deleted=True)

        # 普通用户：只返回有权限的 API
        # 1. 获取所有 API 权限
        all_api_perms = await self.get_api_permissions(db, perm_type=perm_type, exclude_deleted=True)

        # 2. 过滤出用户有权限的 API（直接返回，避免不必要的变量赋值）
        return [perm for perm in all_api_perms if perm.name in permission_names]

    async def restore(self, db: AsyncSession, id: int, cache: object | None = None) -> Permission | None:
        permission = await super().restore(db, id, cache)
        if permission is None:
            return None
        affected_user_ids = await self._query_user_ids_by_permission_id(db, id)
        await self._invalidate_permissions_for_users(affected_user_ids)
        affected_app_ids = await self._query_app_ids_by_permission_id(db, id)
        await self._invalidate_app_permissions_for_apps(affected_app_ids)
        return permission

    async def permanent_delete(self, db: AsyncSession, id: int, cache: object | None = None) -> bool:
        affected_user_ids = await self._query_user_ids_by_permission_id(db, id)
        affected_app_ids = await self._query_app_ids_by_permission_id(db, id)
        success = await super().permanent_delete(db, id, cache)
        if success:
            await self._invalidate_permissions_for_users(affected_user_ids)
            await self._invalidate_app_permissions_for_apps(affected_app_ids)
        return success

    async def _before_permission_update_capture_user_ids(self, context: HookContext) -> None:
        permission = context.params.get("instance")
        if permission is None or getattr(permission, "id", None) is None:
            return
        context.results["affected_user_ids_before"] = await self._query_user_ids_by_permission_id(
            context.session, int(permission.id)
        )

    async def _before_permission_update_capture_app_ids(self, context: HookContext) -> None:
        permission = context.params.get("instance")
        if permission is None or getattr(permission, "id", None) is None:
            return
        context.results["affected_app_ids_before"] = await self._query_app_ids_by_permission_id(
            context.session, int(permission.id)
        )

    async def _after_permission_change_invalidate_user_permissions(self, context: HookContext) -> None:
        permission = context.params.get("instance")
        if permission is None or getattr(permission, "id", None) is None:
            return

        affected_user_ids = set(context.results.get("affected_user_ids_before", set()))
        current_user_ids = await self._query_user_ids_by_permission_id(context.session, int(permission.id))
        affected_user_ids.update(current_user_ids)

        await self._invalidate_permissions_for_users(affected_user_ids)

    async def _after_permission_change_invalidate_app_permissions(self, context: HookContext) -> None:
        permission = context.params.get("instance")
        if permission is None or getattr(permission, "id", None) is None:
            return

        affected_app_ids = set(context.results.get("affected_app_ids_before", set()))
        current_app_ids = await self._query_app_ids_by_permission_id(context.session, int(permission.id))
        affected_app_ids.update(current_app_ids)

        await self._invalidate_app_permissions_for_apps(affected_app_ids)

    async def _query_user_ids_by_permission_id(self, db: AsyncSession, permission_id: int) -> set[int]:
        query = (
            select(user_role.c.user_id)
            .join(role_permission, role_permission.c.role_id == user_role.c.role_id)
            .where(role_permission.c.permission_id == permission_id)
            .distinct()
        )
        result = await db.execute(query)
        return {int(user_id) for user_id in result.scalars().all() if user_id is not None}

    async def _query_app_ids_by_permission_id(self, db: AsyncSession, permission_id: int) -> set[int]:
        from src.app.api_auth.models.relationships import api_app_permissions

        result = await db.execute(
            select(api_app_permissions.c.app_id).where(api_app_permissions.c.permission_id == permission_id).distinct()
        )
        return {int(app_id) for app_id in result.scalars().all() if app_id is not None}

    async def _invalidate_permissions_for_users(self, user_ids: set[int]) -> None:
        if not user_ids:
            return
        cache = get_cache()
        await invalidate_users_permissions(cache, user_ids)

    async def _invalidate_app_permissions_for_apps(self, app_ids: set[int]) -> None:
        if not app_ids:
            return
        from src.app.api_auth.services.permission_service import invalidate_app_permissions

        cache = get_cache()
        for app_id in app_ids:
            await invalidate_app_permissions(cache, app_id)


permission_service = PermissionService()
