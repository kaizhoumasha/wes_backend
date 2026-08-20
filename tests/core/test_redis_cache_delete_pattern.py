from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.database.redis_cache import RedisCache


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
