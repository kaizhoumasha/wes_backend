"""RedisCache 原子写入 primitive 测试。"""

from __future__ import annotations

import time
from typing import Any

import pytest

from src.database.redis_cache import CircuitState, RedisCache


class _FakeRedis:
    """捕获 Redis set 调用参数。"""

    def __init__(self, *, set_result: bool | None = True, set_error: Exception | None = None) -> None:
        self.set_result = set_result
        self.set_error = set_error
        self.set_calls: list[dict[str, Any]] = []

    async def ping(self) -> bool:
        return True

    async def set(self, key: str, value: str, *, nx: bool, ex: int) -> bool | None:
        self.set_calls.append({"key": key, "value": value, "nx": nx, "ex": ex})
        if self.set_error is not None:
            raise self.set_error
        return self.set_result


@pytest.mark.asyncio
async def test_set_if_absent_uses_atomic_set_nx_with_fixed_ttl() -> None:
    """安全 nonce 场景必须使用 Redis SET NX EX 固定 TTL，不走随机抖动。"""

    redis = _FakeRedis(set_result=True)
    cache = RedisCache(redis=redis, prefix="cache")

    result = await cache.set_if_absent("api_auth:nonce:app_test:nonce-1", "1", expire=300)

    assert result is True
    assert redis.set_calls == [
        {
            "key": "cache:api_auth:nonce:app_test:nonce-1",
            "value": '"1"',
            "nx": True,
            "ex": 300,
        }
    ]


@pytest.mark.asyncio
async def test_set_if_absent_returns_false_when_key_already_exists() -> None:
    """Redis SET NX 未写入时表示 key 已存在。"""

    redis = _FakeRedis(set_result=None)
    cache = RedisCache(redis=redis, prefix="cache")

    result = await cache.set_if_absent("api_auth:nonce:app_test:nonce-1", "1", expire=300)

    assert result is False
    assert redis.set_calls[0]["nx"] is True


@pytest.mark.asyncio
async def test_set_if_absent_returns_none_when_cache_unavailable() -> None:
    """缓存不可用时返回 None，让安全调用方 fail closed。"""

    redis = _FakeRedis()
    cache = RedisCache(redis=redis, prefix="cache")
    cache.circuit_breaker.state = CircuitState.OPEN
    cache.circuit_breaker.last_failure_time = time.time()

    result = await cache.set_if_absent("api_auth:nonce:app_test:nonce-1", "1", expire=300)

    assert result is None
    assert redis.set_calls == []


@pytest.mark.asyncio
async def test_set_if_absent_returns_none_when_redis_set_fails() -> None:
    """Redis 写入异常时返回 None，而不是当成 nonce 已存在。"""

    redis = _FakeRedis(set_error=RuntimeError("redis write failed"))
    cache = RedisCache(redis=redis, prefix="cache")

    result = await cache.set_if_absent("api_auth:nonce:app_test:nonce-1", "1", expire=300)

    assert result is None
    assert redis.set_calls[0]["ex"] == 300
