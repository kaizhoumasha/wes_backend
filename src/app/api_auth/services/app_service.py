import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api_auth.constants import CacheExpire, CacheKeys
from src.app.api_auth.models import APIApplication
from src.app.api_auth.repositories import APIAppRepository, api_app_repository
from src.core.base_service import BaseService
from src.core.encryption import encryption_service
from src.database.redis_cache import RedisCache


class APIAppService(BaseService[APIApplication, APIAppRepository]):
    def __init__(self):
        super().__init__(
            repository=api_app_repository,
            enable_cache=True,
            cache_prefix="api_app:detail",
            cache_expire=CacheExpire.APP_DETAIL,
        )

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
