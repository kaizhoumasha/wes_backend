import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.admin.models import Permission
from src.app.api_auth.constants import CacheExpire, CacheKeys
from src.app.api_auth.models.relationships import api_app_permissions
from src.core.logger import logger
from src.database.redis_cache import RedisCache


async def get_app_permissions(db: AsyncSession, cache: RedisCache, app_id: int) -> set[str]:
    cache_key = CacheKeys.app_permissions(app_id)

    cached = await cache.get(cache_key)
    if cached:
        try:
            return set(json.loads(cached))
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"权限缓存解析失败: {cache_key}")
            await cache.delete(cache_key)

    result = await db.execute(
        select(Permission.name)  # type: ignore[attr-defined]
        .join(api_app_permissions, api_app_permissions.c.permission_id == Permission.id)
        .where(api_app_permissions.c.app_id == app_id)
        .where(Permission.is_deleted.is_(False))  # type: ignore[attr-defined]
    )
    permissions = {row[0] for row in result.all()}

    await cache.set(cache_key, json.dumps(list(permissions)), expire=CacheExpire.APP_PERMISSIONS)

    return permissions


async def invalidate_app_permissions(cache: RedisCache, app_id: int) -> None:
    cache_key = CacheKeys.app_permissions(app_id)
    await cache.delete(cache_key)
    logger.debug(f"清除应用权限缓存: {cache_key}")
