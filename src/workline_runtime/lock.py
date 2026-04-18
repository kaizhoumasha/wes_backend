"""
Redis 分布式锁实现

提供基于 Redis 的分布式锁功能，用于编排器并发控制。

特性：
- 基于 Redis SET NX 实现互斥锁
- 支持 TTL 自动过期（默认 30 秒）
- 支持重试获取锁
- 支持自动续期（处理长时间运行的任务）
- 支持 Redis 故障时降级到 PostgreSQL advisory 事务锁

设计参考:
- CEO Decision #1: Redis 故障降级到 PostgreSQL advisory 事务锁
- CEO Decision #3: 锁自动续期（处理 >15s 续期）
"""

import asyncio
import hashlib
import uuid
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from redis.asyncio import Redis


def _resolve_ttl_seconds(ttl: timedelta | int | None, *, default_ttl: int) -> int:
    if isinstance(ttl, timedelta):
        return int(ttl.total_seconds())
    if ttl is None:
        return default_ttl
    return ttl


def _decode_redis_token(value: Any) -> str | None:
    if isinstance(value, bytes):
        return value.decode()
    if isinstance(value, str):
        return value
    return None


def _stable_resource_id(resource: str) -> int:
    """为 PostgreSQL advisory lock 生成跨进程稳定的资源 ID。"""

    digest = hashlib.blake2b(resource.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, byteorder="big", signed=False) % (2**63)


class LockAcquireError(Exception):
    """锁获取失败异常"""


class LockReleaseError(Exception):
    """锁释放失败异常"""


@dataclass
class RedisDistributedLock:
    """
    Redis 分布式锁

    使用 Redis SET NX 命令实现分布式锁，支持：
    - 自动过期（TTL）
    - 重试机制
    - 自动续期
    - PostgreSQL 降级

    Attributes:
        redis_client: Redis 客户端实例
        key_prefix: 锁的 key 前缀
        retry_interval: 重试间隔（秒）
        max_retries: 最大重试次数
        default_ttl: 默认 TTL（秒）
        auto_renewal: 是否启用自动续期
        renewal_interval: 续期间隔（秒）
        fallback_to_pg: 是否在 Redis 故障时降级到 PostgreSQL

    Example:
        ```python
        lock = RedisDistributedLock(redis_client=redis, key_prefix="workline:")
        async with lock.acquire("session:123"):
            # 执行需要互斥的操作
            pass
        ```
    """

    redis_client: Redis
    key_prefix: str = "workline:lock:"
    retry_interval: float = 0.1
    max_retries: int = 100
    default_ttl: int = 30
    auto_renewal: bool = False
    renewal_interval: float = 10.0
    fallback_to_pg: bool = True
    pg_lock_func: str = "pg_advisory_xact_lock"

    # 内部状态
    _renewal_active: bool = field(default=False, init=False, repr=False)
    _renewal_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)

    @asynccontextmanager
    async def acquire(
        self,
        resource: str,
        ttl: timedelta | int | None = None,
        db: Any = None,
    ):
        """
        获取分布式锁

        Args:
            resource: 锁定的资源标识
            ttl: 锁的过期时间（默认使用 default_ttl）
            db: 数据库连接（用于 PostgreSQL 降级）

        Yields:
            None

        Raises:
            LockAcquireError: 获取锁失败

        Example:
            ```python
            async with lock.acquire("session:123", ttl=60):
                # 执行需要互斥的操作
                pass
            ```
        """
        key = f"{self.key_prefix}{resource}"
        token = str(uuid.uuid4())

        ttl_seconds = _resolve_ttl_seconds(ttl, default_ttl=self.default_ttl)

        acquired = False
        used_pg_fallback = False

        # 尝试获取 Redis 锁
        try:
            for _ in range(self.max_retries):
                try:
                    acquired = await self.redis_client.set(key, token, nx=True, ex=ttl_seconds)
                    if acquired:
                        break
                except (ConnectionError, OSError) as e:
                    if self.fallback_to_pg and db is not None:
                        # 降级到 PostgreSQL 事务级 advisory lock
                        await self._pg_lock_acquire(db, resource)
                        used_pg_fallback = True
                        acquired = True
                        break
                    else:
                        raise LockAcquireError(f"Redis unavailable: {e}") from e

                await asyncio.sleep(self.retry_interval)

            if not acquired:
                raise LockAcquireError(f"Failed to acquire lock: {resource}")

            # 启动自动续期任务
            if self.auto_renewal and not used_pg_fallback:
                self._renewal_active = True
                self._renewal_task = asyncio.create_task(self._auto_renew(key, token, ttl_seconds))

            try:
                yield
            finally:
                # 停止自动续期
                if self._renewal_task:
                    self._renewal_active = False
                    _ = self._renewal_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await self._renewal_task
                    self._renewal_task = None

                # 释放锁
                if used_pg_fallback:
                    await self._pg_lock_release(db, resource)
                else:
                    _ = await self._release(key, token)

        except LockAcquireError:
            raise
        except Exception as e:
            if isinstance(e, (ConnectionError, OSError)) and self.fallback_to_pg and db is not None:
                # Redis 故障时降级
                await self._pg_lock_acquire(db, resource)
                try:
                    yield
                finally:
                    await self._pg_lock_release(db, resource)
            else:
                # 其他异常（包括用户代码异常）直接抛出，不转换
                raise

    async def _release(self, key: str, token: str) -> bool:
        """
        释放锁

        使用 Lua 脚本确保只有锁的持有者才能释放锁。

        Args:
            key: 锁的 key
            token: 锁的 token

        Returns:
            bool: 是否成功释放
        """
        lua_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        try:
            result = await self.redis_client.eval(lua_script, 1, key, token)  # type: ignore[misc]
            if isinstance(result, bool):
                return result
            if isinstance(result, (int, float, str, bytes)):
                return bool(result)
            return False
        except Exception:
            # 释放失败时静默处理，锁会自动过期
            return False

    async def _auto_renew(self, key: str, token: str, ttl: int) -> None:
        """
        自动续期任务

        在后台定期延长锁的 TTL。

        Args:
            key: 锁的 key
            token: 锁的 token
            ttl: 新的 TTL 值
        """
        while self._renewal_active:
            await asyncio.sleep(self.renewal_interval)
            if not self._renewal_active:
                break
            try:
                # 检查是否仍持有锁
                current = await self.redis_client.get(key)
                current_token = _decode_redis_token(current)

                if current_token == token:
                    await self.redis_client.expire(key, ttl)
                else:
                    # 锁已丢失，停止续期
                    self._renewal_active = False
                    break
            except Exception:
                # Redis 错误时停止续期
                self._renewal_active = False
                break

    async def _pg_lock_acquire(self, db: Any, resource: str) -> None:
        """
        使用 PostgreSQL advisory 锁（降级方案）

        Args:
            db: 数据库连接
            resource: 资源标识
        """
        # 使用稳定哈希，确保多进程/多 worker 的 advisory lock 锁键一致。
        resource_id = _stable_resource_id(resource)

        if self.pg_lock_func == "pg_advisory_xact_lock":
            # 事务级锁，随事务结束自动释放
            await db.execute(f"SELECT pg_advisory_xact_lock({resource_id})")
        else:
            # 兼容极少数显式要求会话级锁的调用方；正常 fallback 应优先事务级锁。
            await db.execute(f"SELECT pg_advisory_lock({resource_id})")

    async def _pg_lock_release(self, db: Any, resource: str) -> None:
        """
        释放 PostgreSQL advisory 锁

        Args:
            db: 数据库连接
            resource: 资源标识
        """
        resource_id = _stable_resource_id(resource)

        if self.pg_lock_func == "pg_advisory_xact_lock":
            # 事务级锁随 commit/rollback 自动释放，禁止再依赖手动 unlock。
            return

        await db.execute(f"SELECT pg_advisory_unlock({resource_id})")


__all__ = [
    "LockAcquireError",
    "LockReleaseError",
    "RedisDistributedLock",
]
