"""
缓存装饰器

提供简单的装饰器来简化缓存逻辑，避免在每个函数中重复编写缓存代码。
"""

# ruff: noqa: SIM108, RET504
# - SIM108: 优先使用 if-else 而不是三元运算符，提高可读性
# - RET504: 直接返回变量比先赋值再返回更清晰

from collections.abc import Callable
from functools import wraps
from typing import TYPE_CHECKING, ParamSpec, TypeVar

from pydantic import BaseModel

from src.core.exceptions import NotFoundException
from src.core.logger import logger
from src.database.redis_cache import RedisCache

if TYPE_CHECKING:
    from src.database.redis_cache import RedisCache

P = ParamSpec("P")
R = TypeVar("R")


def cached(
    key_prefix: str,
    expire: int = 3600,
    lock: bool = True,
    null_expire: int = 300,
    is_hot: bool = True,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    缓存装饰器

    自动处理缓存读取、锁机制、缓存写入等逻辑。

    Args:
        key_prefix: 缓存键前缀
        expire: 缓存过期时间（秒），默认 1 小时
        lock: 是否使用分布式锁，默认 True
        null_expire: 空值缓存过期时间（秒），默认 5 分钟
        is_hot: 是否标记为热数据，默认 True

    Example:
        @cached(key_prefix="user:detail", expire=7200, lock=True)
        async def get_user_with_cache(
            db: AsyncSession,
            cache: RedisCache,
            user_id: int,
        ) -> UserResponse:
            user = await UserService.get_user_by_id(db, user_id)
            if not user:
                raise NotFoundException("用户不存在")
            return UserService.user_to_response(user)
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            # 从 kwargs 中获取 cache 参数
            cache: RedisCache | None = kwargs.get("cache")
            if not cache:
                # 如果没有 cache，直接调用原函数
                return await func(*args, **kwargs)

            # 构建缓存键（跳过 cache 参数，使用其他参数）
            cache_args = [arg for i, arg in enumerate(args) if i != 1]  # 跳过 db 参数
            cache_key_parts = [str(arg) for arg in cache_args]
            cache_key = f"{key_prefix}:{':'.join(cache_key_parts)}"

            # 尝试从缓存获取
            cached_data = await cache.get(cache_key)
            if cached_data is not None:
                logger.info(f"缓存命中: {cache_key}")
                # 如果返回值是 Pydantic 模型，需要反序列化
                if hasattr(func, "__annotations__") and "return" in func.__annotations__:
                    return_annotation = func.__annotations__["return"]
                    # 检查是否是 Pydantic 模型
                    if hasattr(return_annotation, "model_validate"):
                        return return_annotation.model_validate(cached_data)
                return cached_data

            # 如果需要锁
            if lock:
                lock_acquired = await cache.acquire_lock(cache_key, timeout=10, wait_timeout=5)
                if lock_acquired:
                    try:
                        # 双重检查
                        cached_data = await cache.get(cache_key)
                        if cached_data is not None:
                            return cached_data

                        # 执行函数
                        result = await func(*args, **kwargs)

                        # 序列化结果（如果是 Pydantic 模型）
                        if isinstance(result, BaseModel):
                            serialized = result.model_dump(mode="json")
                        else:
                            serialized = result

                        await cache.set(cache_key, serialized, expire=expire, is_hot=is_hot)
                        return result
                    finally:
                        await cache.release_lock(cache_key)
                else:
                    logger.warning(f"获取锁失败，降级到数据库查询: {cache_key}")
                    # 获取锁失败，直接查询
                    result = await func(*args, **kwargs)
                    return result
            else:
                # 无锁模式
                result = await func(*args, **kwargs)

                # 序列化结果
                if isinstance(result, BaseModel):
                    serialized = result.model_dump(mode="json")
                else:
                    serialized = result

                await cache.set(cache_key, serialized, expire=expire, is_hot=is_hot)
                return result

        return wrapper

    return decorator


def cached_null(
    key_prefix: str,
    expire: int = 3600,
    null_expire: int = 300,
    lock: bool = True,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    缓存装饰器（支持空值缓存）

    与 `cached` 不同，此装饰器会缓存 None 值，防止缓存穿透。

    Args:
        key_prefix: 缓存键前缀
        expire: 缓存过期时间（秒）
        null_expire: 空值缓存过期时间（秒）
        lock: 是否使用分布式锁

    Example:
        @cached_null(key_prefix="user:detail", expire=7200, null_expire=300)
        async def get_user_with_cache(
            db: AsyncSession,
            cache: RedisCache,
            user_id: int,
        ) -> UserResponse | None:
            return await UserService.get_user_by_id(db, user_id)
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            cache: RedisCache | None = kwargs.get("cache")
            if not cache:
                return await func(*args, **kwargs)

            # 构建缓存键
            cache_args = [arg for i, arg in enumerate(args) if i != 1]
            cache_key_parts = [str(arg) for arg in cache_args]
            cache_key = f"{key_prefix}:{':'.join(cache_key_parts)}"

            # 尝试从缓存获取
            cached_data = await cache.get(cache_key)
            if cached_data is not None:
                logger.info(f"缓存命中: {cache_key}")
                # 空值缓存
                if cached_data is None:
                    raise NotFoundException("资源不存在")

                if hasattr(func, "__annotations__") and "return" in func.__annotations__:
                    return_annotation = func.__annotations__["return"]
                    if hasattr(return_annotation, "model_validate"):
                        return return_annotation.model_validate(cached_data)
                return cached_data

            # 使用锁机制
            if lock:
                lock_acquired = await cache.acquire_lock(cache_key, timeout=10, wait_timeout=5)
                if lock_acquired:
                    try:
                        cached_data = await cache.get(cache_key)
                        if cached_data is not None:
                            if cached_data is None:
                                raise NotFoundException("资源不存在")
                            return cached_data

                        # 执行函数
                        result = await func(*args, **kwargs)

                        # 空值缓存
                        if result is None:
                            await cache.set(cache_key, None, expire=null_expire)
                            raise NotFoundException("资源不存在")

                        # 序列化结果
                        if isinstance(result, BaseModel):
                            serialized = result.model_dump(mode="json")
                        else:
                            serialized = result

                        await cache.set(cache_key, serialized, expire=expire, is_hot=True)
                        return result
                    finally:
                        await cache.release_lock(cache_key)
                else:
                    logger.warning(f"获取锁失败，降级到数据库查询: {cache_key}")
                    result = await func(*args, **kwargs)
                    if result is None:
                        raise NotFoundException("资源不存在")
                    return result
            else:
                result = await func(*args, **kwargs)
                if result is None:
                    await cache.set(cache_key, None, expire=null_expire)
                    raise NotFoundException("资源不存在")

                if isinstance(result, BaseModel):
                    serialized = result.model_dump(mode="json")
                else:
                    serialized = result

                await cache.set(cache_key, serialized, expire=expire, is_hot=True)
                return result

        return wrapper

    return decorator


__all__ = ["cached", "cached_null"]
