import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.admin.models import Permission
from src.app.api_auth.constants import CacheExpire, CacheKeys
from src.app.api_auth.models import APIApplication
from src.app.api_auth.models.api_application import AppStatus, ValidityPeriod
from src.app.api_auth.repositories import APIAppRepository, api_app_repository
from src.core.base_service import BaseService
from src.core.encryption import encryption_service
from src.database.hooks import HookContext, HookType
from src.database.redis_cache import RedisCache
from src.utils.timezone import timezone


class APIAppService(BaseService[APIApplication, APIAppRepository]):
    def __init__(self):
        super().__init__(
            repository=api_app_repository,
            enable_cache=True,
            cache_prefix="api_app:detail",
            cache_expire=CacheExpire.APP_DETAIL,
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

    async def reset_secret(self, db: AsyncSession, cache: RedisCache, id: int) -> str:
        """重置应用密钥"""
        app_secret = f"sec_{secrets.token_urlsafe(32)}"
        encrypted_secret = encryption_service.encrypt(app_secret)

        await self.update(db, id, {"app_secret_encrypted": encrypted_secret}, cache)
        return app_secret

    async def assign_permissions(self, db: AsyncSession, cache: RedisCache, id: int, permission_ids: list[int]) -> None:
        """分配权限"""
        from src.app.api_auth.models.relationships import api_app_permissions

        # 1. 验证应用存在
        app = await self.repo.get_by_id(db, id)
        if not app:
            raise ValueError(f"应用 {id} 不存在")

        # 2. 删除旧的权限关联
        await db.execute(
            api_app_permissions.delete().where(api_app_permissions.c.app_id == id)
        )

        # 3. 插入新的权限关联
        if permission_ids:
            await db.execute(
                api_app_permissions.insert(),
                [{"app_id": id, "permission_id": pid} for pid in permission_ids],
            )

        await db.commit()

        # 4. 清除缓存
        await self.invalidate_cache(cache, id)
        await cache.delete(CacheKeys.app_permissions(id))

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

        await self.invalidate_cache(cache, application_id, invalidate_list=True)

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
        )

    async def get_remaining_days(self, app: APIApplication) -> int | None:
        """计算剩余天数"""
        if app.expires_at is None:
            return None

        delta = app.expires_at - timezone.now_for_db()
        return max(0, delta.days)

    async def create_app(
        self, db: AsyncSession, data: dict, cache: RedisCache | None = None
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

        cached = await cache.get(cache_key)
        if cached:
            return APIApplication.model_validate_json(cached)

        result = await db.execute(
            select(APIApplication).where(APIApplication.app_id == app_id).where(APIApplication.is_deleted.is_(False))  # type: ignore[attr-defined]
        )
        app = result.scalar_one_or_none()

        if app:
            await cache.set(cache_key, app.model_dump_json(), expire=CacheExpire.APP_DETAIL)

        return app

    async def revoke_app(self, db: AsyncSession, app_id: str, cache: RedisCache) -> bool:
        app = await self.get_by_app_id(db, cache, app_id)
        if not app or not app.id:
            return False

        await self.update(db, app.id, {"status": "revoked"}, cache)

        if cache:
            await cache.delete(CacheKeys.app_by_app_id(app_id))
            await cache.delete(CacheKeys.app_permissions(app.id))

        return True


api_app_service = APIAppService()
