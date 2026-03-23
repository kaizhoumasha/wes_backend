import secrets
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api_auth.constants import CacheKeys
from src.app.api_auth.models import APIApplication
from src.app.api_auth.models.api_application import AppStatus, ValidityPeriod
from src.app.api_auth.repositories import APIAppRepository, api_app_repository
from src.common.cache_config import cache_settings
from src.core.base_service import BaseService
from src.core.encryption import encryption_service
from src.database.cache_helpers import get_cached_value, set_cached_value
from src.database.hooks import HookContext, HookType
from src.database.redis_cache import RedisCache
from src.utils.timezone import timezone


class APIAppService(BaseService[APIApplication, APIAppRepository]):
    def __init__(self):
        super().__init__(
            repository=api_app_repository,
            enable_cache=True,
            cache_prefix=cache_settings.API_APP.prefix,
            cache_expire=cache_settings.API_APP.expire,
            list_cache_prefix=cache_settings.API_APP_LIST.prefix,
            list_cache_expire=cache_settings.API_APP_LIST.expire,
        )
        # 注册创建前 Hook：自动计算过期时间
        self.add_hook(
            HookType.BEFORE_CREATE,
            self._calculate_expires_at,
            priority=0,
        )

    async def _calculate_expires_at(self, context: HookContext) -> None:
        """创建时根据 validity_period 自动计算 expires_at"""
        data = context.params.get("data", {})

        validity_period = data.get("validity_period", ValidityPeriod.ONE_YEAR)
        delta = validity_period.to_timedelta()

        if delta is None:
            # 永不过期：使用 PostgreSQL 的最大日期
            data["expires_at"] = None
        else:
            # 基于当前时间计算过期时间
            data["expires_at"] = timezone.now_for_db() + delta

    async def _load_app_for_cache_invalidation(
        self,
        db: AsyncSession,
        application_id: int,
        *,
        include_deleted: bool = True,
    ) -> APIApplication | None:
        """加载应用以获取 app_id，用于别名缓存失效。"""
        return await self.repo.get_by_id(db, application_id, include_deleted=include_deleted)

    async def _invalidate_app_alias_cache(self, cache: RedisCache | None, app_id: str | None) -> None:
        """失效按 app_id 查询的别名缓存。"""
        if cache is None or not app_id:
            return
        _ = await cache.delete(CacheKeys.app_by_app_id(app_id))

    async def _invalidate_app_cache_entries(
        self,
        cache: object | None,
        *,
        application_id: int | None = None,
        app_id: str | None = None,
        invalidate_list: bool = False,
        invalidate_permissions: bool = False,
    ) -> None:
        """统一失效应用相关缓存。"""
        if not isinstance(cache, RedisCache):
            return

        if application_id is not None:
            _ = await self.invalidate_cache(cache, application_id, invalidate_list=invalidate_list)
            if invalidate_permissions:
                _ = await cache.delete(CacheKeys.app_permissions(application_id))

        await self._invalidate_app_alias_cache(cache, app_id)

    async def _invalidate_app_permission_cache(self, cache: object | None, application_id: int | None) -> None:
        """失效应用权限缓存。"""
        if application_id is None or cache is None or not hasattr(cache, "delete"):
            return
        await cast("Any", cache).delete(CacheKeys.app_permissions(application_id))

    @staticmethod
    def _parse_cached_app(value: Any) -> APIApplication:
        """解析 app_id 别名缓存中的应用对象。"""
        if isinstance(value, dict):
            return APIApplication.model_validate(value)
        return APIApplication.model_validate_json(value)

    async def _query_by_app_id(self, db: AsyncSession, app_id: str) -> APIApplication | None:
        """根据 app_id 查询应用（委托给 Repository）"""
        return await self.repo.get_by_app_id(db, app_id)

    async def update(
        self,
        db: AsyncSession,
        id: int,
        data: dict[str, object],
        cache: object | None = None,
    ) -> APIApplication | None:
        app = await self._load_app_for_cache_invalidation(db, id)
        result = await super().update(db, id, data, cache)
        await self._invalidate_app_cache_entries(cache, app_id=getattr(app, "app_id", None))
        return result

    async def delete(self, db: AsyncSession, id: int, cache: object | None = None) -> bool | None:
        app = await self._load_app_for_cache_invalidation(db, id)
        success = await super().delete(db, id, cache)
        if success:
            await self._invalidate_app_cache_entries(cache, app_id=getattr(app, "app_id", None))
        return success

    async def soft_delete(self, db: AsyncSession, id: int, cache: object | None = None) -> APIApplication | None:
        app = await self._load_app_for_cache_invalidation(db, id)
        result = await super().soft_delete(db, id, cache)
        if result is not None:
            await self._invalidate_app_cache_entries(cache, app_id=getattr(app, "app_id", None))
        return result

    async def restore(self, db: AsyncSession, id: int, cache: object | None = None) -> APIApplication | None:
        app = await self._load_app_for_cache_invalidation(db, id)
        result = await super().restore(db, id, cache)
        if result is not None:
            await self._invalidate_app_cache_entries(cache, app_id=getattr(app, "app_id", None))
        return result

    async def permanent_delete(self, db: AsyncSession, id: int, cache: object | None = None) -> bool:
        app = await self._load_app_for_cache_invalidation(db, id)
        success = await super().permanent_delete(db, id, cache)
        if success:
            await self._invalidate_app_cache_entries(cache, app_id=getattr(app, "app_id", None))
        return success

    async def reset_secret(self, db: AsyncSession, cache: RedisCache, id: int) -> str:
        """重置应用密钥"""
        app_secret = f"sec_{secrets.token_urlsafe(32)}"
        encrypted_secret = encryption_service.encrypt(app_secret)

        _ = await self.update(db, id, {"app_secret_encrypted": encrypted_secret}, cache)
        return app_secret

    async def assign_permissions(self, db: AsyncSession, cache: RedisCache, id: int, permission_ids: list[int]) -> None:
        """分配权限"""
        # 1. 验证应用存在
        app = await self.repo.get_by_id(db, id)
        if not app:
            raise ValueError(f"应用 {id} 不存在")

        # 2. 委托给 Repository 处理关联表操作
        await self.repo.assign_permissions(db, id, permission_ids)

        # 3. 清除缓存
        _ = await self.invalidate_cache(cache, id)
        _ = await self._invalidate_app_permission_cache(cache, id)

    async def reset_validity_period(
        self,
        db: AsyncSession,
        cache: RedisCache,
        application_id: int,
        validity_period: ValidityPeriod,
        version: int,
    ) -> APIApplication | None:
        """重置有效期：基于 timezone.now_for_db() 重新计算 expires_at

        Args:
            db: 数据库会话
            cache: 缓存
            application_id: 应用 ID
            validity_period: 新的有效期时长
            version: 乐观锁版本号

        Returns:
            更新后的应用对象
        """
        app = await self.get_by_id(db, cache, application_id)

        if not app:
            raise ValueError(f"应用 {application_id} 不存在")

        # 计算新的过期时间（基于创建时间，而不是当前时间）
        delta = validity_period.to_timedelta()

        new_expires_at = delta if delta is None else timezone.now_for_db() + delta

        # 如果应用已过期，自动恢复为 ACTIVE 状态
        new_status = app.status
        if app.status == AppStatus.EXPIRED and (new_expires_at is None or new_expires_at > timezone.now_for_db()):
            new_status = AppStatus.ACTIVE

        # 更新应用
        return await self.update(
            db,
            application_id,
            {
                "validity_period": validity_period,
                "expires_at": new_expires_at,
                "status": new_status,
                "version": version,
            },
            cache,
        )

    async def get_remaining_days(self, app: APIApplication) -> int | None:
        """计算剩余天数"""
        if app.expires_at is None:
            return None

        delta = app.expires_at - timezone.now_for_db()
        return max(0, delta.days)

    async def create_app(
        self, db: AsyncSession, data: dict[str, Any], cache: RedisCache | None = None
    ) -> tuple[APIApplication | None, str]:
        app_id = f"app_{secrets.token_urlsafe(12)}"
        app_secret = f"sec_{secrets.token_urlsafe(32)}"

        data["app_id"] = app_id
        data["app_secret_encrypted"] = encryption_service.encrypt(app_secret)
        data.setdefault("status", "active")
        data.setdefault("rate_limit_per_minute", 100)
        data.setdefault("rate_limit_per_hour", 5000)

        app = await self.create(db, data, cache)
        return app, app_secret

    async def get_by_app_id(self, db: AsyncSession, cache: RedisCache, app_id: str) -> APIApplication | None:
        cache_key = CacheKeys.app_by_app_id(app_id)

        hit, cached = await get_cached_value(
            cache,
            cache_key,
            parser=self._parse_cached_app,
        )
        if hit:
            return cached

        app = await self._query_by_app_id(db, app_id)

        if app:
            _ = await set_cached_value(cache, cache_key, app, expire=self.cache_expire)
        else:
            _ = await set_cached_value(cache, cache_key, None, null_expire=self.null_cache_expire)

        return app

    async def revoke_app(self, db: AsyncSession, app_id: str, cache: RedisCache) -> bool:
        app = await self.get_by_app_id(db, cache, app_id)
        if app is None or app.id is None:
            return False

        _ = await self.update(db, app.id, {"status": "revoked"}, cache)
        _ = await self._invalidate_app_permission_cache(cache, app.id)

        return True


api_app_service = APIAppService()
