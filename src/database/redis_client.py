"""
Redis 连接管理器（支持优雅降级 + 自动重连）

当 Redis 不可用时，应用可以正常启动，缓存功能会自动降级。
当 Redis 恢复时，系统会自动检测并重新连接，恢复缓存功能。
"""

import asyncio
import time
from typing import Any, cast

from redis.asyncio import ConnectionPool, Redis
from redis.exceptions import AuthenticationError, ConnectionError, MaxConnectionsError, TimeoutError

from src.core.conf import settings
from src.core.logger import logger


class RedisManager:
    """
    Redis 连接管理器（支持优雅降级 + 自动重连）
    """

    def __init__(self):
        self.redis_client: Redis | None = None
        self.connection_pool: ConnectionPool | None = None
        self.is_available: bool = False  # Redis 是否可用
        self._last_reconnect_attempt: float = 0  # 上次重连尝试时间
        self._reconnect_interval: int = 30  # 重连间隔（秒）
        self._loop_id: int | None = None  # 创建当前 Redis 客户端的事件循环
        self._owner_loop: asyncio.AbstractEventLoop | None = None
        self._init_lock: asyncio.Lock | None = None
        self._init_lock_loop: asyncio.AbstractEventLoop | None = None

    def _assert_owner_loop(self) -> asyncio.AbstractEventLoop:
        """拒绝在非资源 owner loop 上探活、清理或重建连接。"""
        current_loop = asyncio.get_running_loop()
        if self._owner_loop is not None and current_loop is not self._owner_loop:
            raise RuntimeError("RedisManager owner event loop mismatch; refusing foreign-loop access")
        return current_loop

    def _get_init_lock(self, current_loop: asyncio.AbstractEventLoop) -> asyncio.Lock:
        """在首次使用的事件循环中懒创建 single-flight 锁。"""
        if self._init_lock is None or (
            self._owner_loop is None and self._init_lock_loop is not current_loop and not self._init_lock.locked()
        ):
            self._init_lock = asyncio.Lock()
            self._init_lock_loop = current_loop
        elif self._init_lock_loop is not current_loop:
            raise RuntimeError("RedisManager initialization lock belongs to a foreign event loop")
        return self._init_lock

    @staticmethod
    async def _close_resources(client: Redis | None, pool: ConnectionPool | None) -> None:
        """尽力关闭一组已发布或尚未发布的 Redis 资源。"""
        pending_base_exception: BaseException | None = None
        if client is not None:
            try:
                await client.close()
            except BaseException as exc:
                if isinstance(exc, Exception):
                    logger.debug(f"清理 Redis client 时出错（可忽略）: type={type(exc).__name__}, error={exc!r}")
                else:
                    pending_base_exception = exc
        if pool is not None:
            try:
                await pool.disconnect()
            except BaseException as exc:
                if isinstance(exc, Exception):
                    logger.debug(f"清理 Redis pool 时出错（可忽略）: type={type(exc).__name__}, error={exc!r}")
                elif pending_base_exception is None:
                    pending_base_exception = exc
        if pending_base_exception is not None:
            raise pending_base_exception

    @staticmethod
    def _observe_candidate_cleanup(task: asyncio.Task[None]) -> None:
        try:
            task.result()
        except BaseException as exc:
            logger.warning(f"Redis 未发布候选资源异步清理未完成: type={type(exc).__name__}, error={exc!r}")

    async def _close_cancelled_candidates(
        self,
        client: Redis | None,
        pool: ConnectionPool | None,
        cleanup_timeout: float | None,
    ) -> None:
        if cleanup_timeout is None:
            await self._close_resources(client, pool)
            return

        cleanup_task = asyncio.create_task(self._close_resources(client, pool))
        done, _ = await asyncio.wait({cleanup_task}, timeout=max(cleanup_timeout, 0.0))
        if cleanup_task in done:
            try:
                cleanup_task.result()
            except BaseException as exc:
                logger.warning(f"Redis 未发布候选资源清理异常: type={type(exc).__name__}, error={exc!r}")
            return

        logger.warning(
            f"Redis 未发布候选资源清理预算耗尽，转为 child 内 best-effort 清理: cleanup_timeout={cleanup_timeout:.3f}s"
        )
        cleanup_task.add_done_callback(self._observe_candidate_cleanup)

    async def init_redis(self, *, cancel_cleanup_timeout: float | None = None) -> None:
        """
        初始化 Redis 连接

        如果 Redis 连接失败，只记录警告，不阻断应用启动。
        应用将以降级模式运行（直接查询数据库）。
        支持幂等调用：已连接时跳过。
        """
        current_loop = self._assert_owner_loop()
        init_lock = self._get_init_lock(current_loop)
        candidate_pool: ConnectionPool | None = None
        candidate_client: Redis | None = None
        try:
            async with init_lock:
                self._assert_owner_loop()

                # 锁外等待者复用先完成的初始化结果，不再创建第二个候选连接。
                if self.is_available and self.redis_client is not None:
                    try:
                        await cast("Any", self.redis_client).ping()
                        return
                    except Exception:
                        await self._cleanup_owned_resources()
                else:
                    await self._cleanup_owned_resources()

                connection_pool_cls = cast("Any", ConnectionPool)
                candidate_pool = cast(
                    "ConnectionPool",
                    connection_pool_cls.from_url(
                        settings.REDIS_URL,
                        db=0,
                        decode_responses=True,
                        max_connections=50,
                        retry_on_timeout=True,
                        health_check_interval=30,
                    ),
                )
                candidate_client = Redis(connection_pool=candidate_pool)

                # 候选资源 ping 成功后才一次性发布，失败时不会暴露半初始化状态。
                await cast("Any", candidate_client).ping()
                self.connection_pool = candidate_pool
                self.redis_client = candidate_client
                self._loop_id = id(current_loop)
                self._owner_loop = current_loop
                self.is_available = True
                candidate_pool = None
                candidate_client = None
                logger.info("✓ Redis 连接成功")

        except (AuthenticationError, TimeoutError, ConnectionError) as e:
            await self._close_resources(candidate_client, candidate_pool)
            logger.warning(
                f"⚠️  Redis 连接失败: type={type(e).__name__}, error={e!r}\n"
                "   应用将以降级模式运行（无缓存）\n   系统将自动检测 Redis 恢复并重连"
            )

        except asyncio.CancelledError:
            # 普通调用在返回前完成候选清理；仅 worker 显式预算耗尽时 detach，
            # 且独立清理任务只持有局部候选，不再触碰 manager 发布状态。
            await self._close_cancelled_candidates(candidate_client, candidate_pool, cancel_cleanup_timeout)
            raise

        except Exception as e:
            await self._close_resources(candidate_client, candidate_pool)
            logger.warning(
                f"⚠️  Redis 初始化发生未知错误: type={type(e).__name__}, error={e!r}\n"
                "   应用将以降级模式运行（无缓存）\n"
                "   系统将自动检测 Redis 恢复并重连"
            )

    async def init_redis_with_cleanup_budget(self, cleanup_timeout: float) -> None:
        """供 worker 初始化使用：在明确剩余预算内清理取消的未发布候选资源。"""
        await self.init_redis(cancel_cleanup_timeout=cleanup_timeout)

    async def reconnect(self, *, force: bool = False) -> bool:
        """
        尝试重新连接 Redis

        :param force: 是否忽略重连频率限制，连接池耗尽等确定性故障需要立即清理
        :return: 重连是否成功
        """
        self._assert_owner_loop()

        # 限制重连频率（避免过于频繁）
        current_time = time.time()
        if not force and current_time - self._last_reconnect_attempt < self._reconnect_interval:
            return False

        self._last_reconnect_attempt = current_time
        logger.info("🔄 尝试重新连接 Redis...")

        # init_redis 在 single-flight 锁内完成旧资源清理和候选发布。
        await self.init_redis()

        if self.is_available:
            logger.info("✅ Redis 重连成功，缓存功能已恢复")
        else:
            logger.warning("⚠️  Redis 重连失败，继续保持降级模式")

        return self.is_available

    async def ensure_connection(self) -> bool:
        """
        确保 Redis 连接可用

        如果连接不可用，尝试重连（有频率限制）

        :return: Redis 是否可用
        """
        self._assert_owner_loop()

        if self.is_available and self.redis_client:
            try:
                # 快速检查连接
                ping_result = cast("Any", self.redis_client).ping()
                if asyncio.iscoroutine(ping_result):
                    await asyncio.wait_for(ping_result, timeout=1.0)
                elif not ping_result:
                    raise ConnectionError("Redis ping failed")
                return True
            except MaxConnectionsError:
                logger.warning("Redis 连接池耗尽，强制重建连接池...")
                self.is_available = False
                await self._cleanup()
                return await self.reconnect(force=True)
            except TimeoutError:
                logger.warning("Redis 连接超时，尝试重连...")
                self.is_available = False
                return await self.reconnect()
            except Exception:
                logger.warning("Redis 连接中断，尝试重连...")
                self.is_available = False
                return await self.reconnect()
        else:
            # Redis 未初始化，尝试重连
            return await self.reconnect()

    async def _cleanup_owned_resources(self) -> None:
        """清理当前 owner loop 发布的连接，并先撤销可用状态。"""
        client = self.redis_client
        pool = self.connection_pool
        # child 退出有硬时间边界：先撤销全局发布，避免超时清理期间新消息继续取得旧连接。
        self.is_available = False
        self.redis_client = None
        self.connection_pool = None
        self._loop_id = None
        self._owner_loop = None
        await self._close_resources(client, pool)

    async def _cleanup(self) -> None:
        """清理旧连接。"""
        current_loop = self._assert_owner_loop()
        async with self._get_init_lock(current_loop):
            self._assert_owner_loop()
            await self._cleanup_owned_resources()

    async def close_redis(self) -> None:
        """
        关闭 Redis 连接
        """
        await self._cleanup()
        logger.info("Redis 连接已关闭")

    def get_redis(self) -> Redis | None:
        """
        获取 Redis 客户端实例

        :return: Redis 客户端，如果不可用返回 None
        """
        if self.redis_client is None:
            return None

        try:
            current_loop_id = id(asyncio.get_running_loop())
        except RuntimeError:
            return self.redis_client

        if self._loop_id is not None and current_loop_id != self._loop_id:
            logger.warning("检测到跨事件循环复用 Redis 客户端，自动降级为无缓存模式")
            return None

        return self.redis_client

    def is_redis_available(self) -> bool:
        """
        检查 Redis 是否可用

        :return: Redis 是否可用
        """
        return self.is_available


# 全局 Redis 管理器实例
redis_manager = RedisManager()


async def init_redis() -> None:
    """初始化 Redis（非阻塞）"""
    await redis_manager.init_redis()


async def close_redis() -> None:
    """关闭 Redis 连接"""
    await redis_manager.close_redis()


def get_redis() -> Redis | None:
    """获取 Redis 客户端（可能为 None）"""
    return redis_manager.get_redis()


def is_redis_available() -> bool:
    """检查 Redis 是否可用"""
    return redis_manager.is_redis_available()


async def ensure_redis_connection() -> bool:
    """
    确保 Redis 连接可用（自动重连）

    :return: Redis 是否可用
    """
    return await redis_manager.ensure_connection()
