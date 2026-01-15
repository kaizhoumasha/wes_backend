from redis.asyncio import ConnectionPool, Redis
from redis.exceptions import AuthenticationError, TimeoutError, ConnectionError

from src.core.conf import settings
from src.core.logger import logger


class RedisManager:
    """
    Redis 连接管理器
    """
    def __init__(self):
        self.redis_client: Redis | None = None
        self.connection_pool: ConnectionPool | None = None

    async def init_redis(self) -> None:
        """
        初始化 Redis 连接
        """
        try:
            self.connection_pool = ConnectionPool.from_url(
                settings.REDIS_URL,
                db=0,
                decode_responses=True,
                max_connections=50,
                retry_on_timeout=True,
                health_check_interval=30
            )
            self.redis_client = Redis(connection_pool=self.connection_pool)
            
            # 测试连接
            await self.redis_client.ping()
            logger.info("Redis connection initialized successfully")
            
        except (AuthenticationError, TimeoutError, ConnectionError) as e:
            logger.error(f"Redis connection failed: {e}")
            # 如果是启动时连接失败，抛出异常阻断启动
            raise e
        except Exception as e:
            logger.error(f"Redis initialization occurred unknown error: {e}")
            raise e

    async def close_redis(self) -> None:
        """
        关闭 Redis 连接
        """
        try:
            if self.redis_client:
                await self.redis_client.close()
                logger.info("Redis client closed")
                
            if self.connection_pool:
                await self.connection_pool.disconnect()
                logger.info("Redis connection pool disconnected")
                
        except Exception as e:
            logger.error(f"Error closing Redis connection: {e}")

    def get_redis(self) -> Redis:
        """
        获取 Redis 客户端实例
        """
        if not self.redis_client:
            raise RuntimeError("Redis is not initialized")
        return self.redis_client


# 全局 Redis 管理器实例
redis_manager = RedisManager()


async def init_redis() -> None:
    await redis_manager.init_redis()


async def close_redis() -> None:
    await redis_manager.close_redis()


def get_redis() -> Redis:
    return redis_manager.get_redis()
