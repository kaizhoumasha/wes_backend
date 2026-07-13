#!/usr/bin/env python3
"""
Redis 自动重连测试脚本

演示 Redis 故障恢复时，系统如何自动重连并恢复缓存功能
"""

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


async def demo_redis_reconnection() -> None:
    """测试 Redis 自动重连"""
    from src.database.redis_cache import get_cache
    from src.database.redis_client import ensure_redis_connection

    print("=" * 60)
    print("Redis 自动重连测试")
    print("=" * 60)
    print()

    # 获取缓存实例
    cache = get_cache()

    # 1. 测试正常情况
    print("1. 测试当前状态")
    print("-" * 60)
    status = cache.get_status()
    print(f"缓存状态: {status}")
    print(f"Redis 可用: {cache.redis is not None}")
    print()

    # 2. 测试缓存操作
    print("2. 测试缓存操作")
    print("-" * 60)
    test_key = "test:reconnection"
    test_value = {"data": "test", "timestamp": 1234567890}

    # 设置缓存
    set_result = await cache.set(test_key, test_value)
    print(f"设置缓存: {'✓ 成功' if set_result else '✗ 失败（降级）'}")

    # 读取缓存
    cached_value = await cache.get(test_key)
    if cached_value:
        print(f"读取缓存: ✓ 成功, value={cached_value}")
    else:
        print("读取缓存: ✗ 失败或降级")
    print()

    # 3. 尝试触发重连
    print("3. 测试自动重连机制")
    print("-" * 60)
    print("调用 ensure_redis_connection()...")

    reconnected = await ensure_redis_connection()
    print(f"重连结果: {'✓ 成功' if reconnected else '✗ 失败'}")
    print()

    # 4. 再次测试缓存操作
    print("4. 重连后测试缓存操作")
    print("-" * 60)
    cached_value = await cache.get(test_key)
    if cached_value:
        print(f"读取缓存: ✓ 成功, value={cached_value}")
    else:
        print("读取缓存: ✗ 失败或降级")
    print()

    # 5. 显示最终状态
    print("5. 最终状态")
    print("-" * 60)
    status = cache.get_status()
    print(f"缓存状态: {status}")
    print(f"Redis 可用: {cache.redis is not None}")
    print(f"熔断器状态: {status['circuit_breaker_state']}")
    print()

    print("=" * 60)
    print("测试完成")
    print("=" * 60)
    print()
    print("提示：")
    print("1. 如果 Redis 当前不可用：")
    print("   - 启动 Redis: docker-compose start redis")
    print("   - 等待 30 秒后重运行此测试")
    print()
    print("2. 如果 Redis 当前可用：")
    print("   - 停止 Redis: docker-compose stop redis")
    print("   - 等待几秒后启动 Redis")
    print("   - 再次运行此测试，观察自动重连")
    print()


if __name__ == "__main__":
    try:
        asyncio.run(demo_redis_reconnection())
    except KeyboardInterrupt:
        print("\n\n测试已中断")


class _CandidatePool:
    def __init__(self) -> None:
        self.disconnect = AsyncMock()


class _CandidateClient:
    def __init__(self, *, ping: Any) -> None:
        self.ping = ping
        self.close = AsyncMock()


def _install_candidates(
    monkeypatch: pytest.MonkeyPatch,
    *,
    client_factory: Any,
) -> tuple[Any, list[_CandidatePool], list[_CandidateClient]]:
    from src.database import redis_client as redis_module

    pools: list[_CandidatePool] = []
    clients: list[_CandidateClient] = []

    class PoolFactory:
        @classmethod
        def from_url(cls, *_args: object, **_kwargs: object) -> _CandidatePool:
            pool = _CandidatePool()
            pools.append(pool)
            return pool

    def build_client(*, connection_pool: _CandidatePool) -> _CandidateClient:
        client = client_factory(connection_pool)
        clients.append(client)
        return client

    monkeypatch.setattr(redis_module, "ConnectionPool", PoolFactory)
    monkeypatch.setattr(redis_module, "Redis", build_client)
    return redis_module, pools, clients


@pytest.mark.asyncio
async def test_candidate_pool_and_client_are_published_only_after_ping_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.database.redis_client import RedisManager

    manager = RedisManager()
    published_during_ping: list[bool] = []

    async def ping() -> bool:
        published_during_ping.append(manager.connection_pool is not None or manager.redis_client is not None)
        return True

    _redis_module, pools, clients = _install_candidates(
        monkeypatch,
        client_factory=lambda _pool: _CandidateClient(ping=ping),
    )

    await manager.init_redis()

    assert published_during_ping == [False]
    assert manager.connection_pool is pools[0]
    assert manager.redis_client is clients[0]
    assert manager.is_available is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [RedisTimeoutError("timeout"), RedisConnectionError("ping failed"), ValueError("generic ping error")],
)
async def test_ping_failure_closes_unpublished_candidate_resources(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    from src.database.redis_client import RedisManager

    async def ping() -> None:
        raise error

    _redis_module, pools, clients = _install_candidates(
        monkeypatch,
        client_factory=lambda _pool: _CandidateClient(ping=ping),
    )
    manager = RedisManager()

    await manager.init_redis()

    clients[0].close.assert_awaited_once()
    pools[0].disconnect.assert_awaited_once()
    assert manager.redis_client is None
    assert manager.connection_pool is None
    assert manager.is_available is False


@pytest.mark.asyncio
async def test_cancelled_ping_closes_unpublished_candidate_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.database.redis_client import RedisManager

    async def ping() -> None:
        raise asyncio.CancelledError

    _redis_module, pools, clients = _install_candidates(
        monkeypatch,
        client_factory=lambda _pool: _CandidateClient(ping=ping),
    )
    manager = RedisManager()

    with pytest.raises(asyncio.CancelledError):
        await manager.init_redis()

    clients[0].close.assert_awaited_once()
    pools[0].disconnect.assert_awaited_once()
    assert manager.redis_client is None
    assert manager.connection_pool is None
    assert manager.is_available is False


@pytest.mark.asyncio
async def test_concurrent_init_and_reconnect_share_one_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.database.redis_client import RedisManager

    ping_started = asyncio.Event()
    release_ping = asyncio.Event()

    async def ping() -> bool:
        ping_started.set()
        await release_ping.wait()
        return True

    _redis_module, pools, clients = _install_candidates(
        monkeypatch,
        client_factory=lambda _pool: _CandidateClient(ping=ping),
    )
    manager = RedisManager()

    initialize = asyncio.create_task(manager.init_redis())
    reconnect: asyncio.Task[None] | None = None
    try:
        await asyncio.wait_for(ping_started.wait(), timeout=1.0)
        reconnect = asyncio.create_task(manager.reconnect(force=True))
        await asyncio.sleep(0)
        release_ping.set()
        await asyncio.wait_for(asyncio.gather(initialize, reconnect), timeout=1.0)
    finally:
        release_ping.set()
        tasks = [task for task in (initialize, reconnect) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    assert len(pools) == 1
    assert len(clients) == 1
    assert manager.connection_pool is pools[0]
    assert manager.redis_client is clients[0]


def test_initialized_manager_rejects_foreign_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.database.redis_client import RedisManager

    async def ping() -> bool:
        return True

    _install_candidates(monkeypatch, client_factory=lambda _pool: _CandidateClient(ping=ping))
    manager = RedisManager()
    asyncio.run(manager.init_redis())

    with pytest.raises(RuntimeError, match=r"(?i)(owner|foreign|event loop|loop)"):
        asyncio.run(manager.init_redis())


def test_reconnect_rejects_foreign_event_loop_without_cleaning_owned_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.database.redis_client import RedisManager

    async def ping() -> bool:
        return True

    _redis_module, pools, clients = _install_candidates(
        monkeypatch,
        client_factory=lambda _pool: _CandidateClient(ping=ping),
    )
    manager = RedisManager()
    asyncio.run(manager.init_redis())

    with pytest.raises(RuntimeError, match=r"(?i)(owner|foreign|event loop|loop)"):
        asyncio.run(manager.reconnect(force=True))

    assert manager.connection_pool is pools[0]
    assert manager.redis_client is clients[0]
    clients[0].close.assert_not_awaited()
    pools[0].disconnect.assert_not_awaited()


@pytest.mark.asyncio
async def test_close_and_init_share_single_flight_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.database.redis_client import RedisManager

    async def ping() -> bool:
        return True

    _redis_module, pools, clients = _install_candidates(
        monkeypatch,
        client_factory=lambda _pool: _CandidateClient(ping=ping),
    )
    manager = RedisManager()
    await manager.init_redis()
    close_started = asyncio.Event()
    release_close = asyncio.Event()

    async def blocking_close() -> None:
        close_started.set()
        await release_close.wait()

    clients[0].close.side_effect = blocking_close
    closing = asyncio.create_task(manager.close_redis())
    initializing: asyncio.Task[None] | None = None
    try:
        await asyncio.wait_for(close_started.wait(), timeout=1.0)
        initializing = asyncio.create_task(manager.init_redis())
        await asyncio.sleep(0)
        assert len(pools) == 1
        release_close.set()
        await asyncio.wait_for(asyncio.gather(closing, initializing), timeout=1.0)
    finally:
        release_close.set()
        tasks = [task for task in (closing, initializing) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    assert len(pools) == 2
    assert manager.redis_client is clients[1]
    assert manager.is_available is True


@pytest.mark.asyncio
async def test_resource_cleanup_reaches_pool_after_repeated_cancellation() -> None:
    from src.database.redis_client import RedisManager

    first_cancel_seen = asyncio.Event()
    client = _CandidateClient(ping=AsyncMock())
    pool = _CandidatePool()

    async def cancellation_resistant_close() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            first_cancel_seen.set()
            await asyncio.Event().wait()

    client.close.side_effect = cancellation_resistant_close
    cleanup = asyncio.create_task(RedisManager._close_resources(client, pool))
    await asyncio.sleep(0)
    cleanup.cancel()
    await asyncio.wait_for(first_cancel_seen.wait(), timeout=1.0)
    cleanup.cancel()

    with pytest.raises(asyncio.CancelledError):
        await cleanup

    client.close.assert_awaited_once()
    pool.disconnect.assert_awaited_once()
