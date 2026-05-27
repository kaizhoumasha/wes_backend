"""
RedisDistributedLock 单元测试

测试 Redis 分布式锁的核心功能：
- 锁获取（首次成功、重试成功、超时失败）
- 锁释放（正常释放、Token 校验）
- 锁自动续期
- Redis 故障降级到 PostgreSQL 行锁

设计参考:
- 设计文档: phase2-orchestrator design doc
- CEO Decision #1: Redis 故障降级到 PostgreSQL 行锁
- CEO Decision #3: 锁自动续期（处理 >15s 续期）
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.workline_runtime.lock import (
    LockAcquireError,
    LockReleaseError,
    RedisDistributedLock,
)


class TestRedisDistributedLockAcquire:
    """锁获取测试"""

    @pytest.mark.asyncio
    async def test_acquire_success_on_first_try(self):
        """测试首次获取锁成功"""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)

        lock = RedisDistributedLock(redis_client=mock_redis, key_prefix="workline:")

        async with lock.acquire("session:123"):
            # 验证调用了 Redis SET NX
            mock_redis.set.assert_called_once()
            args, kwargs = mock_redis.set.call_args
            assert args[0] == "workline:session:123"
            assert kwargs.get("nx") is True
            assert kwargs.get("ex") == 30  # 默认 TTL

    @pytest.mark.asyncio
    async def test_acquire_success_after_retry(self):
        """测试重试后获取锁成功"""
        mock_redis = AsyncMock()
        # 前两次失败，第三次成功
        mock_redis.set = AsyncMock(side_effect=[False, False, True])

        lock = RedisDistributedLock(
            redis_client=mock_redis,
            key_prefix="workline:",
            retry_interval=0.01,  # 加快测试
            max_retries=10,
        )

        async with lock.acquire("session:456"):
            # 验证重试了 3 次
            assert mock_redis.set.call_count == 3

    @pytest.mark.asyncio
    async def test_acquire_timeout_after_max_retries(self):
        """测试超过最大重试次数后抛出异常"""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=False)  # 始终失败

        lock = RedisDistributedLock(
            redis_client=mock_redis,
            key_prefix="workline:",
            retry_interval=0.01,
            max_retries=3,
        )

        with pytest.raises(LockAcquireError, match="Failed to acquire lock"):
            async with lock.acquire("session:789"):
                pass

        # 验证重试了 3 次
        assert mock_redis.set.call_count == 3

    @pytest.mark.asyncio
    async def test_acquire_with_custom_ttl(self):
        """测试自定义 TTL"""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)

        lock = RedisDistributedLock(redis_client=mock_redis, key_prefix="workline:")

        async with lock.acquire("session:abc", ttl=60):
            _, kwargs = mock_redis.set.call_args
            assert kwargs.get("ex") == 60


class TestRedisDistributedLockRelease:
    """锁释放测试"""

    @pytest.mark.asyncio
    async def test_release_own_lock_success(self):
        """测试释放自己的锁成功"""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)

        # 模拟 Lua 脚本返回 1（成功）
        mock_redis.eval = AsyncMock(return_value=1)

        lock = RedisDistributedLock(redis_client=mock_redis, key_prefix="workline:")

        async with lock.acquire("session:123"):
            pass  # 退出时自动释放

        # 验证调用了 Lua 脚本释放
        mock_redis.eval.assert_called_once()

    @pytest.mark.asyncio
    async def test_release_fails_when_token_mismatch(self):
        """测试 Token 不匹配时释放失败（锁已被他人持有或过期）"""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)

        # 模拟 Lua 脚本返回 0（Token 不匹配）
        mock_redis.eval = AsyncMock(return_value=0)

        lock = RedisDistributedLock(redis_client=mock_redis, key_prefix="workline:")

        # 不应该抛出异常，但应该记录日志
        async with lock.acquire("session:456"):
            pass

        # 验证尝试了释放
        mock_redis.eval.assert_called_once()


class TestRedisDistributedLockRenewal:
    """锁自动续期测试"""

    @pytest.mark.asyncio
    async def test_auto_renewal_extends_ttl(self):
        """测试自动续期延长 TTL"""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.get = AsyncMock(return_value=b"test-token")  # 返回 token 字节
        mock_redis.expire = AsyncMock(return_value=True)
        mock_redis.eval = AsyncMock(return_value=1)

        lock = RedisDistributedLock(
            redis_client=mock_redis,
            key_prefix="workline:",
            auto_renewal=True,
            renewal_interval=0.1,  # 加快测试
        )

        # 保持锁 0.3 秒，应该触发 2-3 次续期检查
        async with lock.acquire("session:123"):
            await asyncio.sleep(0.3)

        # 验证 get 被调用（检查锁是否仍持有）
        # 自动续期会检查 get 然后调用 expire
        assert mock_redis.get.call_count >= 1

    @pytest.mark.asyncio
    async def test_auto_renewal_disabled_by_default(self):
        """测试默认不启用自动续期"""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.eval = AsyncMock(return_value=1)

        lock = RedisDistributedLock(
            redis_client=mock_redis,
            key_prefix="workline:",
        )

        async with lock.acquire("session:456"):
            await asyncio.sleep(0.2)

        # 验证没有调用 expire
        mock_redis.expire.assert_not_called()


class TestRedisDistributedLockDegradation:
    """Redis 故障降级测试"""

    @pytest.mark.asyncio
    async def test_fallback_to_postgres_lock_on_redis_failure(self):
        """测试 Redis 故障时降级到 PostgreSQL 事务级 advisory lock。"""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(side_effect=ConnectionError("Redis unavailable"))

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()

        lock = RedisDistributedLock(
            redis_client=mock_redis,
            key_prefix="workline:",
            fallback_to_pg=True,
        )

        async with lock.acquire("session:789", db=mock_db):
            pass

        mock_redis.set.assert_called_once()
        statements = [str(call.args[0]) for call in mock_db.execute.call_args_list]
        assert len(statements) == 1
        assert "pg_advisory_xact_lock" in statements[0]

    @pytest.mark.asyncio
    async def test_fallback_to_postgres_lock_on_redis_failure_real_db(self, db_session):
        """测试 Redis 故障时使用真实的 SQLAlchemy session 进行 PostgreSQL 回退。"""
        import sqlalchemy.exc

        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(side_effect=ConnectionError("Redis unavailable"))

        lock = RedisDistributedLock(
            redis_client=mock_redis,
            key_prefix="workline:",
            fallback_to_pg=True,
        )

        # SQLite 没有 pg_advisory_xact_lock 函数，这里会抛出 OperationalError。
        # 这证明 db.execute 成功接受了 text() 对象并发送给数据库执行，
        # 从而排除了使用字符串传入导致 SQLAlchemy 2.x ArgumentError 的问题。
        with pytest.raises(sqlalchemy.exc.OperationalError, match="no such function: pg_advisory_xact_lock"):
            async with lock.acquire("session:real_db", db=db_session):
                pass

    @pytest.mark.asyncio
    async def test_no_fallback_when_disabled(self):
        """测试禁用降级时直接抛出异常"""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(side_effect=ConnectionError("Redis unavailable"))

        lock = RedisDistributedLock(
            redis_client=mock_redis,
            key_prefix="workline:",
            fallback_to_pg=False,
        )

        with pytest.raises(LockAcquireError, match="Redis unavailable"):
            async with lock.acquire("session:abc"):
                pass

    @pytest.mark.asyncio
    async def test_pg_lock_uses_stable_resource_id(self):
        """测试 PostgreSQL 回退使用稳定资源 ID，避免跨进程 hash 漂移。"""
        mock_db = MagicMock()
        mock_db.execute = AsyncMock()

        lock = RedisDistributedLock(redis_client=AsyncMock(), key_prefix="workline:")

        await lock._pg_lock_acquire(mock_db, "session:stable")
        await lock._pg_lock_release(mock_db, "session:stable")
        await lock._pg_lock_acquire(mock_db, "session:stable")

        statements = [str(call.args[0]) for call in mock_db.execute.call_args_list]
        assert len(statements) == 2
        assert statements[0] == statements[1]
        assert "pg_advisory_xact_lock" in statements[0]

    @pytest.mark.asyncio
    async def test_pg_xact_lock_release_is_noop(self):
        """事务级 advisory lock 不应再手动发 unlock。"""
        mock_db = MagicMock()
        mock_db.execute = AsyncMock()

        lock = RedisDistributedLock(redis_client=AsyncMock(), key_prefix="workline:")

        await lock._pg_lock_release(mock_db, "session:noop")

        mock_db.execute.assert_not_called()


class TestOrchestratorPgFallbackProvider:
    """worker 层 PG fallback provider 测试。"""

    @pytest.mark.asyncio
    async def test_build_orchestrator_lock_provider_uses_xact_lock_without_unlock(self):
        """Redis 缺席时，worker provider 应使用事务级 advisory lock，且不再手动 unlock。"""
        from src.celery_app.tasks.workline import _build_orchestrator_lock_provider

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()

        with patch("src.celery_app.tasks.workline.get_redis", return_value=None):
            provider = _build_orchestrator_lock_provider(mock_db)
            async with provider("session:provider"):
                pass

        statements = [str(call.args[0]) for call in mock_db.execute.call_args_list]
        assert len(statements) == 1
        assert "pg_advisory_xact_lock" in statements[0]
        assert "pg_advisory_unlock" not in statements[0]


class TestRedisDistributedLockContext:
    """上下文管理器测试"""

    @pytest.mark.asyncio
    async def test_lock_released_on_exception(self):
        """测试异常发生时锁仍然被释放"""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.eval = AsyncMock(return_value=1)

        lock = RedisDistributedLock(redis_client=mock_redis, key_prefix="workline:")

        with pytest.raises(ValueError):
            async with lock.acquire("session:exception"):
                raise ValueError("Simulated error")

        # 验证锁被释放
        mock_redis.eval.assert_called_once()

    @pytest.mark.asyncio
    async def test_lock_released_on_normal_exit(self):
        """测试正常退出时锁被释放"""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.eval = AsyncMock(return_value=1)

        lock = RedisDistributedLock(redis_client=mock_redis, key_prefix="workline:")

        async with lock.acquire("session:normal"):
            pass

        # 验证锁被释放
        mock_redis.eval.assert_called_once()
