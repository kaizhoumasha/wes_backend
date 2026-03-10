from __future__ import annotations

import pytest

from src.database.redis_cache import RedisCache


class _FakeRedis:
    async def ping(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_random_expire_has_lower_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = RedisCache(redis=None, prefix="app")

    def fake_randint(low: int, high: int) -> int:
        assert low == 1
        assert high == 420
        return low

    monkeypatch.setattr("src.database.redis_cache.random.randint", fake_randint)

    assert cache._random_expire(120, variance=300) == 1


@pytest.mark.asyncio
async def test_is_available_returns_true_immediately_after_reconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = RedisCache(redis=None, prefix="app")
    fake_redis = _FakeRedis()

    async def fake_ensure_connection() -> bool:
        return True

    monkeypatch.setattr("src.database.redis_client.ensure_redis_connection", fake_ensure_connection)
    monkeypatch.setattr("src.database.redis_client.get_redis", lambda: fake_redis)

    assert await cache.is_available() is True
    assert cache.redis is fake_redis
