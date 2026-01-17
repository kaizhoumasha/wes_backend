"""
Redis 连接管理器（支持优雅降级 + 自动重连）

当 Redis 不可用时，应用可以正常启动，缓存功能会自动降级。
当 Redis 恢复时，系统会自动检测并重新连接，恢复缓存功能。
"""

import asyncio
import time

from redis.asyncio import ConnectionPool, Redis
from redis.exceptions import AuthenticationError, ConnectionError, TimeoutError

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

    async def init_redis(self) -> None:
        """
        初始化 Redis 连接

        如果 Redis 连接失败，只记录警告，不阻断应用启动。
        应用将以降级模式运行（直接查询数据库）。
        """
        try:
            self.connection_pool = ConnectionPool.from_url(
                settings.REDIS_URL,
                db=0,
                decode_responses=True,
                max_connections=50,
                retry_on_timeout=True,
                health_check_interval=30,
            )
            self.redis_client = Redis(connection_pool=self.connection_pool)

            # 测试连接
            await self.redis_client.ping()
            self.is_available = True
            logger.info("✓ Redis 连接成功")

        except (AuthenticationError, TimeoutError, ConnectionError) as e:
            self.is_available = False
            self.redis_client = None
            self.connection_pool = None
            logger.warning(
                f"⚠️  Redis 连接失败: {e}\n"
                f"   应用将以降级模式运行（无缓存）\n"
                f"   系统将自动检测 Redis 恢复并重连"
            )

        except Exception as e:
            self.is_available = False
            self.redis_client = None
            self.connection_pool = None
            logger.warning(
                f"⚠️  Redis 初始化发生未知错误: {e}\n"
                f"   应用将以降级模式运行（无缓存）\n"
                f"   系统将自动检测 Redis 恢复并重连"
            )

    async def reconnect(self) -> bool:
        """
        尝试重新连接 Redis

        :return: 重连是否成功
        """
        # 限制重连频率（避免过于频繁）
        current_time = time.time()
        if current_time - self._last_reconnect_attempt < self._reconnect_interval:
            return False

        self._last_reconnect_attempt = current_time
        logger.info("🔄 尝试重新连接 Redis...")

        # 先关闭旧连接（如果有）
        await self._cleanup()

        # 尝试初始化新连接
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
        if self.is_available and self.redis_client:
            try:
                # 快速检查连接
                await asyncio.wait_for(self.redis_client.ping(), timeout=1.0)
                return True
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

    async def _cleanup(self) -> None:
        """清理旧连接"""
        try:
            if self.redis_client:
                await self.redis_client.close()
                self.redis_client = None
            if self.connection_pool:
                await self.connection_pool.disconnect()
                self.connection_pool = None
        except Exception as e:
            logger.debug(f"清理旧连接时出错（可忽略）: {e}")

    async def close_redis(self) -> None:
        """
        关闭 Redis 连接
        """
        await self._cleanup()
        self.is_available = False
        logger.info("Redis 连接已关闭")

    def get_redis(self) -> Redis | None:
        """
        获取 Redis 客户端实例

        :return: Redis 客户端，如果不可用返回 None
        """
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
