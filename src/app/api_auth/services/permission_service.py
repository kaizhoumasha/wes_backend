from sqlalchemy.ext.asyncio import AsyncSession

from src.app.admin.repositories.perm_repository import permission_repository
from src.app.api_auth.constants import CacheExpire, CacheKeys
from src.core.logger import logger
from src.database.cache_helpers import get_cached_value, parse_set_from_cached, set_cached_value
from src.database.redis_cache import RedisCache


async def get_app_permissions(db: AsyncSession, cache: RedisCache, app_id: int) -> set[str]:
    """获取应用拥有的权限名称集合（委托给 Repository）

    设计原则:
        - SRP: Service 层专注于缓存管理，数据访问委托给 Repository
        - KISS: 简洁的委托模式，保持代码清晰
        - 可测试性: 可轻松 Mock Repository 层进行单元测试
    """
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

    # 委托给 Repository 层查询数据库
    permissions = await permission_repository.get_permission_names_by_app_id(db, app_id)

    expire = CacheExpire.APP_PERMISSIONS if permissions else CacheExpire.APP_PERMISSIONS_EMPTY
    await set_cached_value(cache, cache_key, list(permissions), expire=expire)

    return permissions


async def invalidate_app_permissions(cache: RedisCache, app_id: int) -> None:
    cache_key = CacheKeys.app_permissions(app_id)
    await cache.delete(cache_key)
    logger.debug(f"清除应用权限缓存: {cache_key}")
