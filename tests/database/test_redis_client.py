from unittest.mock import AsyncMock

import pytest
from redis.exceptions import MaxConnectionsError

from src.database.redis_client import RedisManager


@pytest.mark.asyncio
async def test_ensure_connection_forces_reconnect_when_pool_is_exhausted(monkeypatch):
    """Redis 连接池耗尽时应立即清理并重建，不受普通重连限频影响。"""
    manager = RedisManager()
    manager.redis_client = AsyncMock()
    manager.redis_client.ping.side_effect = MaxConnectionsError("Too many connections")
    manager.connection_pool = AsyncMock()
    manager.is_available = True
    manager._last_reconnect_attempt = 9999999999

    cleanup = AsyncMock()
    init_redis = AsyncMock()

    async def mark_available() -> None:
        manager.is_available = True

    init_redis.side_effect = mark_available
    monkeypatch.setattr(manager, "_cleanup", cleanup)
    monkeypatch.setattr(manager, "init_redis", init_redis)

    result = await manager.ensure_connection()

    assert result is True
    cleanup.assert_awaited_once()
    init_redis.assert_awaited_once()
