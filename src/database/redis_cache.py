"""
Redis 缓存服务（支持自动降级）

功能：
1. 基础缓存操作（get, set, delete）
2. 缓存穿透防护：空值缓存 + 布隆过滤器
3. 缓存击穿防护：分布式锁 + 热点数据延期
4. 缓存雪崩防护：随机过期时间 + 熔断降级
5. 数据一致性：Cache-Aside 模式
6. **自动降级：Redis 故障时自动降级到直接查询数据库**
"""

import asyncio
import json
import random
import time
from enum import Enum
from typing import Any, cast

from redis.asyncio import Redis

from src.core.logger import logger


class CircuitState(Enum):
    """熔断器状态"""

    CLOSED = "closed"  # 正常工作
    OPEN = "open"  # 熔断打开（拒绝请求）
    HALF_OPEN = "half_open"  # 半开（尝试恢复）


class CircuitBreaker:
    """
    熔断器

    当 Redis 连续失败达到阈值时，熔断器打开，暂时停止尝试连接 Redis，
    避免雪崩效应。经过一段时间后进入半开状态，尝试恢复连接。
    """

    def __init__(
        self,
        failure_threshold: int = 5,  # 失败阈值
        timeout: int = 60,  # 熔断打开时长（秒）
        half_open_max_calls: int = 3,  # 半开状态最大尝试次数
    ):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.half_open_max_calls = half_open_max_calls

        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time: float | None = None
        self.half_open_calls = 0

    def record_success(self):
        """记录成功"""
        self.failure_count = 0
        if self.state == CircuitState.HALF_OPEN:
            self.half_open_calls += 1
            if self.half_open_calls >= self.half_open_max_calls:
                # 恢复正常
                self.state = CircuitState.CLOSED
                self.half_open_calls = 0
                logger.info("熔断器已恢复到正常状态")

    def record_failure(self):
        """记录失败"""
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.state == CircuitState.HALF_OPEN:
            # 半开状态失败，重新打开熔断器
            self.state = CircuitState.OPEN
            self.half_open_calls = 0
            logger.warning("熔断器重新打开（半开状态失败）")

        elif self.failure_count >= self.failure_threshold:
            # 达到失败阈值，打开熔断器
            self.state = CircuitState.OPEN
            logger.error(f"熔断器已打开（失败次数: {self.failure_count}，阈值: {self.failure_threshold}）")

    def can_attempt(self) -> bool:
        """检查是否可以尝试请求"""
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            # 检查是否超时，可以进入半开状态
            if self.last_failure_time and (time.time() - self.last_failure_time) >= self.timeout:
                self.state = CircuitState.HALF_OPEN
                self.half_open_calls = 0
                logger.info("熔断器进入半开状态，尝试恢复")
                return True
            return False

        # HALF_OPEN 状态
        return self.half_open_calls < self.half_open_max_calls

    def get_state(self) -> CircuitState:
        """获取当前状态"""
        return self.state


class RedisCache:
    """
    Redis 缓存服务（支持自动降级）
    """

    # 空值缓存标记（用于缓存穿透防护）
    NULL_VALUE = "__NULL__"

    # 默认过期时间（秒）
    DEFAULT_EXPIRE = 3600

    # 空值缓存过期时间（秒）- 较短
    NULL_EXPIRE = 300

    # 热点数据过期时间（秒）- 较长
    HOT_EXPIRE = 7200

    def __init__(self, redis: Redis | None, prefix: str = "cache"):
        """
        初始化缓存服务

        :param redis: Redis 客户端（None 表示 Redis 不可用）
        :param prefix: 键前缀
        """
        self.redis = redis
        self.prefix = prefix
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=5,  # 5 次连续失败后熔断
            timeout=60,  # 熔断 60 秒
            half_open_max_calls=3,  # 半开状态尝试 3 次
        )
        self._last_health_check = 0
        self._is_healthy = redis is not None

    def _make_key(self, key: str) -> str:
        """生成带前缀的键"""
        return f"{self.prefix}:{key}"

    def _random_expire(self, base_expire: int, variance: int = 300) -> int:
        """
        生成随机过期时间（防止缓存雪崩）

        :param base_expire: 基础过期时间
        :param variance: 随机波动范围（秒）
        :return: 实际过期时间
        """
        min_expire = max(1, base_expire - variance)
        max_expire = max(min_expire, base_expire + variance)
        return random.randint(min_expire, max_expire)  # nosec B311 - cache TTL jitter is not security-sensitive

    async def _check_health(self) -> bool:
        """
        检查 Redis 健康状态（支持自动重连）

        :return: 是否健康
        """
        # 如果 Redis 客户端为 None，尝试重连
        if self.redis is None:
            from src.database.redis_client import ensure_redis_connection

            logger.info("检测到 Redis 未初始化，尝试重连...")
            reconnected = await ensure_redis_connection()
            if reconnected:
                # 重连成功，更新 redis 客户端引用
                from src.database.redis_client import get_redis

                self.redis = get_redis()
                self._is_healthy = True
                logger.info("✅ Redis 重连成功，缓存服务已恢复")
                return True
            return False

        # 限制检查频率（最多每 5 秒检查一次）
        current_time = time.time()
        if current_time - self._last_health_check < 5:
            return self._is_healthy

        self._last_health_check = current_time

        try:
            await asyncio.wait_for(self.redis.ping(), timeout=1.0)  # type: ignore[arg-type]
            self._is_healthy = True
            return True
        except (TimeoutError, Exception) as e:
            self._is_healthy = False
            logger.warning(f"Redis 健康检查失败: {e}")

            # 尝试重连
            from src.database.redis_client import ensure_redis_connection

            logger.info("Redis 连接中断，尝试重连...")
            reconnected = await ensure_redis_connection()
            if reconnected:
                # 重连成功，更新 redis 客户端引用
                from src.database.redis_client import get_redis

                self.redis = get_redis()
                self._is_healthy = True
                logger.info("✅ Redis 重连成功，缓存服务已恢复")
                return True

            return False

    async def is_available(self) -> bool:
        """
        检查缓存服务是否可用（支持自动重连）

        考虑熔断器状态和 Redis 健康状态

        :return: 是否可用
        """
        if not self.circuit_breaker.can_attempt():
            return False

        # 健康检查会自动尝试重连
        return await self._check_health()

    async def get(self, key: str) -> Any | None:
        """
        获取缓存（支持自动降级）

        :param key: 缓存键
        :return: 缓存值，不存在或 Redis 不可用返回 None
        """
        if not await self.is_available():
            # Redis 不可用，降级返回 None
            logger.debug(f"Redis 不可用，跳过缓存读取: {key}")
            return None

        redis_key = self._make_key(key)
        try:
            value = await self.redis.get(redis_key)  # type: ignore[arg-type]
            if value is None:
                return None

            # 检查是否是空值标记
            if value == self.NULL_VALUE:
                logger.debug(f"缓存命中（空值）: {key}")
                return None

            logger.debug(f"缓存命中: {key}")
            self.circuit_breaker.record_success()
            return json.loads(value)
        except Exception as e:
            logger.error(f"获取缓存失败: {key}, error: {e}")
            self.circuit_breaker.record_failure()
            return None

    async def set(
        self,
        key: str,
        value: Any,
        expire: int | None = None,
        is_hot: bool = False,
        max_expire: int | None = None,
    ) -> bool:
        """
        设置缓存（支持自动降级）

        :param key: 缓存键
        :param value: 缓存值
        :param expire: 过期时间（秒），None 则使用默认值
        :param is_hot: 是否热点数据（热点数据使用较长过期时间）
        :param max_expire: 实际过期时间上限，None 保持默认随机抖动行为
        :return: 是否成功
        """
        if not await self.is_available():
            # Redis 不可用，降级返回 False（不影响主业务）
            logger.debug(f"Redis 不可用，跳过缓存设置: {key}")
            return False

        redis_key = self._make_key(key)

        # 处理空值（缓存穿透防护）
        if value is None:
            serialized_value = self.NULL_VALUE
            base_expire = expire or self.NULL_EXPIRE
        else:
            serialized_value = json.dumps(value, ensure_ascii=False)
            # 热点数据使用较长过期时间
            base_expire = expire or (self.HOT_EXPIRE if is_hot else self.DEFAULT_EXPIRE)

        # 添加随机过期时间（防止缓存雪崩）
        actual_expire = self._random_expire(base_expire)
        if max_expire is not None:
            actual_expire = min(actual_expire, max(1, int(max_expire)))

        try:
            await self.redis.setex(redis_key, actual_expire, serialized_value)  # type: ignore[arg-type]
            logger.debug(f"缓存设置: {key}, expire: {actual_expire}s")
            self.circuit_breaker.record_success()
            return True
        except Exception as e:
            logger.error(f"设置缓存失败: {key}, error: {e}")
            self.circuit_breaker.record_failure()
            return False

    async def set_if_absent(self, key: str, value: Any, expire: int) -> bool | None:
        """
        仅当 key 不存在时设置缓存，使用固定 TTL，不添加随机抖动。

        该方法用于安全/幂等场景（如 nonce 消费），调用方需要区分：
        True=本次成功写入，False=key 已存在，None=Redis 不可用或写入失败。

        :param key: 缓存键
        :param value: 缓存值
        :param expire: 固定过期时间（秒）
        :return: 是否成功写入；Redis 不可用或异常返回 None
        """
        if not await self.is_available():
            logger.debug(f"Redis 不可用，跳过原子缓存设置: {key}")
            return None

        ttl_seconds = max(1, int(expire))
        redis_key = self._make_key(key)
        serialized_value = self.NULL_VALUE if value is None else json.dumps(value, ensure_ascii=False)

        try:
            result = await self.redis.set(redis_key, serialized_value, nx=True, ex=ttl_seconds)  # type: ignore[arg-type]
            self.circuit_breaker.record_success()
            if result:
                logger.debug(f"原子缓存设置成功: {key}, expire: {ttl_seconds}s")
                return True
            logger.debug(f"原子缓存设置跳过，键已存在: {key}")
            return False
        except Exception as e:
            logger.error(f"原子缓存设置失败: {key}, error: {e}")
            self.circuit_breaker.record_failure()
            return None

    async def delete(self, key: str) -> bool:
        """
        删除缓存（支持自动降级）

        :param key: 缓存键
        :return: 是否成功
        """
        if not await self.is_available():
            # Redis 不可用，降级返回 False（不影响主业务）
            logger.debug(f"Redis 不可用，跳过缓存删除: {key}")
            return False

        redis_key = self._make_key(key)
        try:
            await self.redis.delete(redis_key)  # type: ignore[arg-type]
            logger.debug(f"缓存删除: {key}")
            self.circuit_breaker.record_success()
            return True
        except Exception as e:
            logger.error(f"删除缓存失败: {key}, error: {e}")
            self.circuit_breaker.record_failure()
            return False

    async def delete_pattern(self, pattern: str) -> int:
        """
        批量删除缓存（通配符，支持自动降级）

        :param pattern: 匹配模式
        :return: 删除数量
        """
        if not await self.is_available():
            # Redis 不可用，降级返回 0（不影响主业务）
            logger.debug(f"Redis 不可用，跳过批量删除: {pattern}")
            return 0

        redis_pattern = self._make_key(pattern)
        try:
            keys = [key async for key in self.redis.scan_iter(match=redis_pattern)]  # type: ignore[arg-type]
            if keys:
                result = await self.redis.delete(*keys)  # type: ignore[arg-type]
                self.circuit_breaker.record_success()
                return result
            return 0
        except Exception as e:
            logger.error(f"批量删除缓存失败: {pattern}, error: {e}")
            self.circuit_breaker.record_failure()
            return 0

    async def exists(self, key: str) -> bool:
        """
        检查缓存是否存在（支持自动降级）

        :param key: 缓存键
        :return: 是否存在
        """
        if not await self.is_available():
            return False

        redis_key = self._make_key(key)
        try:
            result = await self.redis.exists(redis_key)  # type: ignore[arg-type]
            self.circuit_breaker.record_success()
            return result > 0
        except Exception as e:
            logger.error(f"检查缓存存在性失败: {key}, error: {e}")
            self.circuit_breaker.record_failure()
            return False

    async def acquire_lock(self, lock_key: str, timeout: int = 10, wait_timeout: int = 5) -> bool:
        """
        获取分布式锁（缓存击穿防护，支持自动降级）

        :param lock_key: 锁键
        :param timeout: 锁超时时间（秒）
        :param wait_timeout: 等待超时时间（秒）
        :return: 是否获取成功
        """
        if not await self.is_available():
            # Redis 不可用，降级返回 False（直接执行查询）
            logger.debug(f"Redis 不可用，跳过分布式锁: {lock_key}")
            return False

        redis_key = self._make_key(f"lock:{lock_key}")
        identifier = str(asyncio.current_task().get_name() if asyncio.current_task() else id(asyncio.current_task()))  # type: ignore[arg-type]

        start_time = asyncio.get_event_loop().time()
        while True:
            # 尝试获取锁
            try:
                acquired = await self.redis.set(redis_key, identifier, nx=True, ex=timeout)  # type: ignore[arg-type]
                if acquired:
                    logger.debug(f"获取锁成功: {lock_key}")
                    self.circuit_breaker.record_success()
                    return True
            except Exception as e:
                logger.error(f"获取锁失败: {lock_key}, error: {e}")
                self.circuit_breaker.record_failure()
                return False

            # 检查超时
            if asyncio.get_event_loop().time() - start_time > wait_timeout:
                logger.warning(f"获取锁超时: {lock_key}")
                return False

            # 短暂休眠
            await asyncio.sleep(0.01)

    async def release_lock(self, lock_key: str) -> bool:
        """
        释放分布式锁（支持自动降级）

        :param lock_key: 锁键
        :return: 是否释放成功
        """
        if not await self.is_available():
            return False

        redis_key = self._make_key(f"lock:{lock_key}")
        try:
            await self.redis.delete(redis_key)  # type: ignore[arg-type]
            logger.debug(f"释放锁: {lock_key}")
            self.circuit_breaker.record_success()
            return True
        except Exception as e:
            logger.error(f"释放锁失败: {lock_key}, error: {e}")
            self.circuit_breaker.record_failure()
            return False

    def get_status(self) -> dict[str, Any]:
        """
        获取缓存服务状态

        :return: 状态信息
        """
        return {
            "available": self._is_healthy,
            "circuit_breaker_state": self.circuit_breaker.get_state().value,
            "failure_count": self.circuit_breaker.failure_count,
            "failure_threshold": self.circuit_breaker.failure_threshold,
        }

    async def incr(self, key: str) -> int | None:
        """
        原子性递增缓存值（支持自动降级）

        :param key: 缓存键
        :return: 递增后的值，如果 Redis 不可用则返回 None
        """
        if not await self.is_available():
            return None

        redis_key = self._make_key(key)
        try:
            result = await self.redis.incr(redis_key)  # type: ignore[arg-type]
            self.circuit_breaker.record_success()
            return int(result)
        except Exception as e:
            logger.error(f"递增缓存失败: {key}, error: {e}")
            self.circuit_breaker.record_failure()
            return None

    async def expire(self, key: str, expire: int):
        if not await self.is_available():
            return

        redis_key = self._make_key(key)
        try:
            await self.redis.expire(redis_key, expire)  # type: ignore[arg-type]
            self.circuit_breaker.record_success()
        except Exception as e:
            logger.error(f"设置缓存过期时间失败: {key}, error: {e}")
            self.circuit_breaker.record_failure()

    async def incr_with_expire(self, key: str, expire: int) -> int | None:
        """
        原子性递增并设置过期时间（支持自动降级）

        :param key: 缓存键
        :param expire: 过期时间（秒）
        :return: 递增后的值，如果 Redis 不可用则返回 None
        """
        if not await self.is_available():
            # Redis 不可用，降级返回 None
            logger.debug(f"Redis 不可用，跳过递增操作: {key}")
            return None

        redis_key = self._make_key(key)
        lua_script = """
        local count = redis.call('INCR', KEYS[1])
        if count == 1 then
            redis.call('EXPIRE', KEYS[1], ARGV[1])
        end
        return count
        """
        try:
            result = await self.redis.eval(lua_script, 1, redis_key, expire)  # type: ignore[arg-type]
            self.circuit_breaker.record_success()
            return int(cast("int | str", result)) if result is not None else None
        except Exception as e:
            logger.error(f"递增缓存失败: {key}, error: {e}")
            self.circuit_breaker.record_failure()
            return None


# 全局缓存实例（延迟初始化）
_cache_instance: RedisCache | None = None


def get_cache() -> RedisCache:
    """
    获取缓存实例（支持降级 + 自动重连）

    如果 Redis 不可用，返回的缓存实例会自动降级所有操作。
    当 Redis 恢复时，会自动检测并重连。
    """
    global _cache_instance
    if _cache_instance is None:
        from src.database.redis_client import get_redis

        redis_client = get_redis()

        if redis_client is not None:
            # Redis 可用，创建正常缓存实例
            _cache_instance = RedisCache(redis_client, prefix="app")
        else:
            # Redis 不可用，创建一个标记为不可用的缓存实例
            # 这样可以避免在 get_cache() 时抛出异常
            # 所有缓存操作会自动降级
            logger.warning("Redis 不可用，缓存服务已降级（将自动检测恢复）")
            _cache_instance = RedisCache(None, prefix="app")  # type: ignore[arg-type]

    return _cache_instance
