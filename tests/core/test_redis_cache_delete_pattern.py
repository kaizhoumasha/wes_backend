from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.core.conf import settings
from src.database import redis_cache, redis_client
from src.database.redis_cache import RedisCache, get_cache
from src.database.redis_namespace import database_redis_cache_prefix


class _Redis:
    def __init__(self, keys: list[str] | None = None, *, error: Exception | None = None) -> None:
        self.keys = keys or []
        self.error = error
        self.delete = AsyncMock(return_value=len(self.keys))

    async def scan_iter(self, *, match: str):
        if self.error is not None:
            raise self.error
        for key in self.keys:
            yield key


class _SharedRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def ping(self) -> bool:
        return True

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def setex(self, key: str, _expire: int, value: str) -> bool:
        self.values[key] = value
        return True


def _get_cache_for_database(
    monkeypatch: pytest.MonkeyPatch,
    shared_redis: _SharedRedis | None,
    database_identity: str,
) -> RedisCache:
    monkeypatch.setattr(settings, "POSTGRES_DB", database_identity)
    monkeypatch.setattr(redis_client, "get_redis", lambda: shared_redis)
    monkeypatch.setattr(redis_cache, "_cache_instance", None)
    return get_cache()


def test_database_redis_cache_prefix_is_stable_opaque_and_database_specific() -> None:
    assert database_redis_cache_prefix("wes_test_42_alpha") == (
        "app:6887dee545cc69b1fe9c666337734a295d2b91d9d435b1de495e7ac65020af3d"
    )
    assert database_redis_cache_prefix("wes_test_43_beta") == (
        "app:4a6e2b92e66c19e30e50b4de832517681135febd4fd431e2f8dd2bd57af838fa"
    )


@pytest.mark.parametrize("database_identity", ("", " WES_DB", "wes-db", "wes:db", "x" * 64))
def test_database_redis_cache_prefix_rejects_invalid_database_identity(database_identity: str) -> None:
    with pytest.raises(ValueError, match="POSTGRES_DB"):
        database_redis_cache_prefix(database_identity)


@pytest.mark.asyncio
async def test_get_cache_isolates_database_identities_sharing_one_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_redis = _SharedRedis()
    first = _get_cache_for_database(monkeypatch, shared_redis, "wes_test_42_alpha")
    assert await first.set("role:1", {"database": "first"}) is True

    second = _get_cache_for_database(monkeypatch, shared_redis, "wes_test_43_beta")
    assert await second.get("role:1") is None

    same_database = _get_cache_for_database(monkeypatch, shared_redis, "wes_test_42_alpha")
    assert await same_database.get("role:1") == {"database": "first"}


@pytest.mark.parametrize("connected", (True, False))
def test_get_cache_uses_database_prefix_when_connected_or_degraded(
    monkeypatch: pytest.MonkeyPatch,
    connected: bool,
) -> None:
    shared_redis: Any = _SharedRedis() if connected else None

    cache = _get_cache_for_database(monkeypatch, shared_redis, "wes_test_42_alpha")

    assert cache.prefix == "app:6887dee545cc69b1fe9c666337734a295d2b91d9d435b1de495e7ac65020af3d"


@pytest.mark.asyncio
async def test_delete_pattern_returns_none_when_redis_is_unavailable() -> None:
    cache = RedisCache(None, prefix="app")
    cache.is_available = AsyncMock(return_value=False)  # type: ignore[method-assign]

    assert await cache.delete_pattern("perms:user:*") is None


@pytest.mark.asyncio
async def test_delete_pattern_returns_zero_for_success_without_matching_keys() -> None:
    redis = _Redis()
    cache = RedisCache(redis, prefix="app")  # type: ignore[arg-type]
    cache.is_available = AsyncMock(return_value=True)  # type: ignore[method-assign]
    cache.circuit_breaker.failure_count = 2

    assert await cache.delete_pattern("perms:user:*") == 0
    assert cache.circuit_breaker.failure_count == 0
    redis.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_pattern_returns_deleted_count_on_success() -> None:
    redis = _Redis(["app:perms:user:1", "app:perms:user:2"])
    cache = RedisCache(redis, prefix="app")  # type: ignore[arg-type]
    cache.is_available = AsyncMock(return_value=True)  # type: ignore[method-assign]

    assert await cache.delete_pattern("perms:user:*") == 2
    redis.delete.assert_awaited_once_with("app:perms:user:1", "app:perms:user:2")


@pytest.mark.asyncio
async def test_delete_pattern_returns_none_when_scan_fails() -> None:
    cache = RedisCache(_Redis(error=RuntimeError("redis down")), prefix="app")  # type: ignore[arg-type]
    cache.is_available = AsyncMock(return_value=True)  # type: ignore[method-assign]

    assert await cache.delete_pattern("perms:user:*") is None
