"""
角色 Service
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.admin.models import Role, user_role
from src.app.admin.repositories.role_repository import role_repository
from src.common.cache_config import cache_settings
from src.core.base_service import BaseService
from src.core.rbac import invalidate_users_permissions
from src.database.base_repository import BaseRepository, HookContext, HookType
from src.database.redis_cache import get_cache


class RoleService(BaseService[Role, BaseRepository]):
    """角色 Service"""

    def __init__(self, repo: BaseRepository = role_repository):
        super().__init__(
            repo,
            enable_cache=True,
            cache_prefix=cache_settings.ROLE.prefix,
            cache_expire=cache_settings.ROLE.expire,
        )

        # 角色变更会影响关联用户权限，统一在 Hook 中失效用户权限缓存
        self.add_hook(HookType.BEFORE_UPDATE, self._before_role_update_capture_user_ids, priority=100)
        self.add_hook(HookType.AFTER_CREATE, self._after_role_change_invalidate_user_permissions, priority=100)
        self.add_hook(HookType.AFTER_UPDATE, self._after_role_change_invalidate_user_permissions, priority=100)
        self.add_hook(HookType.AFTER_DELETE, self._after_role_change_invalidate_user_permissions, priority=100)

    async def restore(self, db: AsyncSession, id: int, cache: object | None = None) -> Role | None:
        role = await super().restore(db, id, cache)
        if role is None:
            return None
        affected_user_ids = await self._query_user_ids_by_role_id(db, id)
        await self._invalidate_permissions_for_users(affected_user_ids)
        return role

    async def permanent_delete(self, db: AsyncSession, id: int, cache: object | None = None) -> bool:
        affected_user_ids = await self._query_user_ids_by_role_id(db, id)
        success = await super().permanent_delete(db, id, cache)
        if success:
            await self._invalidate_permissions_for_users(affected_user_ids)
        return success

    async def _before_role_update_capture_user_ids(self, context: HookContext) -> None:
        role = context.params.get("instance")
        if role is None or getattr(role, "id", None) is None:
            return
        context.results["affected_user_ids_before"] = await self._query_user_ids_by_role_id(
            context.session, int(role.id)
        )

    async def _after_role_change_invalidate_user_permissions(self, context: HookContext) -> None:
        role = context.params.get("instance")
        if role is None or getattr(role, "id", None) is None:
            return

        affected_user_ids = set(context.results.get("affected_user_ids_before", set()))
        current_user_ids = await self._query_user_ids_by_role_id(context.session, int(role.id))
        affected_user_ids.update(current_user_ids)

        await self._invalidate_permissions_for_users(affected_user_ids)

    async def _query_user_ids_by_role_id(self, db: AsyncSession, role_id: int) -> set[int]:
        result = await db.execute(select(user_role.c.user_id).where(user_role.c.role_id == role_id))
        return {int(user_id) for user_id in result.scalars().all() if user_id is not None}

    async def _invalidate_permissions_for_users(self, user_ids: set[int]) -> None:
        if not user_ids:
            return
        cache = get_cache()
        await invalidate_users_permissions(cache, user_ids)


role_service = RoleService()
