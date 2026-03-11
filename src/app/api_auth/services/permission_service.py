from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.admin.models import Permission
from src.app.api_auth.constants import CacheExpire, CacheKeys
from src.app.api_auth.models.relationships import api_app_permissions
from src.core.logger import logger
from src.database.cache_helpers import get_cached_value, parse_set_from_cached, set_cached_value
from src.database.redis_cache import RedisCache


async def get_app_permissions(db: AsyncSession, cache: RedisCache, app_id: int) -> set[str]:
    cache_key = CacheKeys.app_permissions(app_id)

    hit, cached = await get_cached_value(
        cache,
        cache_key,
        parser=parse_set_from_cached,
        on_invalid=lambda key: logger.warning(f"权限缓存解析失败: {key}"),
    )
    if hit:
        if cached is None:
            return set()
        return cached

    result = await db.execute(
        select(Permission)
        .join(api_app_permissions, api_app_permissions.c.permission_id == Permission.id)
        .where(api_app_permissions.c.app_id == app_id)
        .where(Permission.is_deleted.is_(False))  # type: ignore[attr-defined]
    )
    permissions = {row.name for row in result.scalars()}

    expire = CacheExpire.APP_PERMISSIONS if permissions else CacheExpire.APP_PERMISSIONS_EMPTY
    await set_cached_value(cache, cache_key, list(permissions), expire=expire)

    return permissions


async def invalidate_app_permissions(cache: RedisCache, app_id: int) -> None:
    cache_key = CacheKeys.app_permissions(app_id)
    await cache.delete(cache_key)
    logger.debug(f"清除应用权限缓存: {cache_key}")
