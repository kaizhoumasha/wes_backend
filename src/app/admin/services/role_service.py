"""
角色 Service
"""

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.admin.models import Role
from src.app.admin.repositories.role_repository import RoleRepository, role_repository
from src.common.cache_config import cache_settings
from src.core.base_service import BaseService
from src.core.rbac import invalidate_users_permissions
from src.database.base_repository import HookContext
from src.database.redis_cache import get_cache


class RoleService(BaseService[Role, RoleRepository]):
    """角色 Service"""

    def __init__(self, repo: RoleRepository = role_repository):
        super().__init__(
            repo,
            enable_cache=True,
            cache_prefix=cache_settings.ROLE.prefix,
            cache_expire=cache_settings.ROLE.expire,
            list_cache_prefix=cache_settings.ROLE_LIST.prefix,
            list_cache_expire=cache_settings.ROLE_LIST.expire,
        )

    async def get_active_roles_by_ids(self, db: AsyncSession, role_ids: list[int]) -> list[Role]:
        """按 ID 列表获取有效角色。

        统一通过 RoleService 校验角色是否存在或已删除，避免其他 Service 直接访问 RoleRepository。
        """
        from src.core.exceptions import NotFoundException

        if not role_ids:
            return []

        # 批量查询避免 N+1
        roles = await self.repo.get_by_ids(db, role_ids)
        found_ids = {role.id for role in roles}
        requested_ids = list(dict.fromkeys(role_ids))

        # 校验所有角色都存在
        missing_ids = [role_id for role_id in requested_ids if role_id not in found_ids]
        if missing_ids:
            missing_text = ", ".join(str(role_id) for role_id in missing_ids)
            raise NotFoundException(f"角色 {missing_text} 不存在")

        # 校验没有已删除的角色
        deleted_roles = [role for role in roles if role.is_deleted]
        if deleted_roles:
            deleted_text = ", ".join(str(role.id) for role in deleted_roles)
            raise NotFoundException(f"角色 {deleted_text} 已删除")

        return roles

    async def update(
        self,
        db: AsyncSession,
        id: int,
        data: dict[str, object],
        cache: object | None = None,
    ) -> Role | None:
        affected_user_ids_before = await self._query_user_ids_by_role_id(db, id)

        role = await super().update(db, id, data, cache)
        if role is None:
            return None

        affected_user_ids = set(affected_user_ids_before)
        affected_user_ids.update(await self._query_user_ids_by_role_id(db, id))
        await self._invalidate_permissions_for_users(affected_user_ids)
        return role

    async def delete(self, db: AsyncSession, id: int, cache: object | None = None) -> bool | None:
        affected_user_ids = await self._query_user_ids_by_role_id(db, id)
        success = await super().delete(db, id, cache)
        if success:
            await self._invalidate_permissions_for_users(affected_user_ids)
        return success

    async def soft_delete(self, db: AsyncSession, id: int, cache: object | None = None) -> Role | None:
        affected_user_ids = await self._query_user_ids_by_role_id(db, id)
        role = await super().soft_delete(db, id, cache)
        if role is None:
            return None
        await self._invalidate_permissions_for_users(affected_user_ids)
        return role

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
        return await self.repo.get_user_ids_by_role_id(db, role_id)

    async def _invalidate_permissions_for_users(self, user_ids: set[int]) -> None:
        if not user_ids:
            return
        cache = get_cache()
        await invalidate_users_permissions(cache, user_ids)


role_service = RoleService()
