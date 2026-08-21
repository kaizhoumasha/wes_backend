"""Settings-free permission cache namespace repair primitives."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import RedisError

from src.database.redis_namespace import database_redis_cache_prefix

if TYPE_CHECKING:
    from collections.abc import Mapping

PERMISSION_CACHE_NAMESPACES = ("perms:user:*", "api_app:perms:*")


class PermissionCacheNamespaceStore(Protocol):
    async def delete_pattern(self, pattern: str) -> int | None: ...


class AuthorizationCacheInvalidationError(RuntimeError):
    """权限缓存删除结果无法确认。"""

    def __init__(
        self,
        *,
        failed_user_ids: frozenset[int] = frozenset(),
        failed_app_ids: frozenset[int] = frozenset(),
        failed_namespaces: frozenset[str] = frozenset(),
    ) -> None:
        self.failed_user_ids = failed_user_ids
        self.failed_app_ids = failed_app_ids
        self.failed_namespaces = failed_namespaces
        details: list[str] = []
        if failed_user_ids:
            details.append(f"user_ids={sorted(failed_user_ids)}")
        if failed_app_ids:
            details.append(f"app_ids={sorted(failed_app_ids)}")
        if failed_namespaces:
            details.append(f"namespaces={sorted(failed_namespaces)}")
        super().__init__("权限缓存失效未确认: " + ", ".join(details))


async def repair_permission_cache_namespaces(cache: PermissionCacheNamespaceStore) -> None:
    failed_namespaces = {
        namespace for namespace in PERMISSION_CACHE_NAMESPACES if await cache.delete_pattern(namespace) is None
    }
    if failed_namespaces:
        raise AuthorizationCacheInvalidationError(failed_namespaces=frozenset(failed_namespaces))


class _EnvironmentRedisPermissionCache:
    def __init__(self, env: Mapping[str, str]) -> None:
        self._key_prefix = database_redis_cache_prefix(env.get("POSTGRES_DB", ""))
        self._redis = Redis(
            host=env.get("REDIS_HOST", "localhost"),
            port=int(env.get("REDIS_PORT", "6379")),
            password=env.get("REDIS_PASSWORD") or None,
            db=int(env.get("REDIS_DB", "0")),
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
            retry=Retry(NoBackoff(), 0),
        )

    async def delete_pattern(self, pattern: str) -> int | None:
        try:
            keys = [key async for key in self._redis.scan_iter(match=f"{self._key_prefix}:{pattern}")]
            if not keys:
                return 0
            return int(await self._redis.delete(*keys))
        except RedisError:
            return None

    async def close(self) -> None:
        await self._redis.aclose()


async def repair_permission_cache_namespaces_from_environment(env: Mapping[str, str]) -> None:
    cache = _EnvironmentRedisPermissionCache(env)
    try:
        await repair_permission_cache_namespaces(cache)
    finally:
        await cache.close()


__all__ = [
    "PERMISSION_CACHE_NAMESPACES",
    "AuthorizationCacheInvalidationError",
    "PermissionCacheNamespaceStore",
    "repair_permission_cache_namespaces",
    "repair_permission_cache_namespaces_from_environment",
]
