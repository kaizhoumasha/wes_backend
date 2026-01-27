"""
缓存装饰器

提供简单的装饰器来简化缓存逻辑，避免在每个函数中重复编写缓存代码。
"""

# - SIM108: 优先使用 if-else 而不是三元运算符，提高可读性
# - PLR0912: 缓存装饰器需要处理多种情况（锁、空值、序列化等），分支数合理

from collections.abc import Awaitable, Callable
from functools import wraps
from inspect import signature
from typing import TYPE_CHECKING, ParamSpec, TypeVar, cast, get_args, get_origin

from pydantic import BaseModel

from src.core.exceptions import NotFoundException
from src.core.logger import logger

if TYPE_CHECKING:
    from src.database.redis_cache import RedisCache

P = ParamSpec("P")
R = TypeVar("R")

# 空值缓存标记：用于区分"键不存在"和"值为空"
_NULL_CACHE_MARKER = "__NULL_CACHE_MARKER__"


def _get_return_type(func: Callable) -> type | None:
    """
    获取函数的返回类型（处理 Optional 类型）

    Args:
        func: 函数对象

    Returns:
        返回类型，如果无法确定则返回 None
    """
    if not hasattr(func, "__annotations__") or "return" not in func.__annotations__:
        return None

    return_annotation = func.__annotations__["return"]

    # 处理 Optional[T] 或 T | None 类型
    origin = get_origin(return_annotation)
    if origin is not None:
        # 对于 Union 类型（包括 Optional）
        args = get_args(return_annotation)
        if args:
            # 过滤掉 None 类型，返回实际的类型
            non_none_types = [arg for arg in args if arg is not type(None)]
            if non_none_types:
                return non_none_types[0]

    return return_annotation


def cached(
    key_prefix: str,
    expire: int = 3600,
    lock: bool = True,
    null_expire: int | None = None,
    is_hot: bool = True,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """
    缓存装饰器

    自动处理缓存读取、锁机制、缓存写入等逻辑。

    Args:
        key_prefix: 缓存键前缀
        expire: 缓存过期时间（秒），默认 1 小时
        lock: 是否使用分布式锁，默认 True
        null_expire: 空值缓存过期时间（秒），None 表示不缓存空值，默认 None
        is_hot: 是否标记为热数据，默认 True

    Example:
        # 不缓存空值（防止缓存穿透需要自行处理）
        @cached(key_prefix="user:detail", expire=7200)
        async def get_user_with_cache(
            db: AsyncSession,
            cache: RedisCache,
            user_id: int,
        ) -> UserResponse:
            user = await UserService.get_user_by_id(db, user_id)
            if not user:
                raise NotFoundException("用户不存在")
            return UserService.user_to_response(user)

        # 缓存空值（防止缓存穿透）
        @cached(key_prefix="user:detail", expire=7200, null_expire=300)
        async def get_user_with_cache(
            db: AsyncSession,
            cache: RedisCache,
            user_id: int,
        ) -> UserResponse | None:
            return await UserService.get_user_by_id(db, user_id)
    """

    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            # 从 kwargs 中获取 cache 参数
            cache = cast("RedisCache | None", kwargs.get("cache"))
            if not cache:
                # 如果没有 cache，直接调用原函数
                return await func(*args, **kwargs)

            # 构建缓存键（排除 db 和 cache 参数）
            sig = signature(func)
            param_names = list(sig.parameters.keys())

            # 收集用于缓存键的参数
            cache_key_parts = []
            for i, arg in enumerate(args):
                if i < len(param_names):
                    param_name = param_names[i]
                    # 跳过 db 和 cache 参数
                    if param_name not in ("db", "cache"):
                        cache_key_parts.append(str(arg))

            # 添加 kwargs 中的参数（排除 db 和 cache）
            for key, value in kwargs.items():
                if key not in ("db", "cache"):
                    cache_key_parts.append(f"{key}={value}")

            cache_key = f"{key_prefix}:{':'.join(cache_key_parts)}"

            # 尝试从缓存获取
            cached_data = await cache.get(cache_key)

            # 处理缓存命中
            if cached_data is not None:
                logger.info(f"缓存命中: {cache_key}")

                # 空值缓存处理：检查是否是空值标记
                if cached_data == _NULL_CACHE_MARKER:
                    raise NotFoundException("资源不存在")

                # Pydantic 模型反序列化
                return_type = _get_return_type(func)
                if return_type and hasattr(return_type, "model_validate"):
                    return return_type.model_validate(cached_data)

                return cached_data

            # 如果需要锁
            if lock:
                lock_acquired = await cache.acquire_lock(cache_key, timeout=10, wait_timeout=5)
                if lock_acquired:
                    try:
                        # 双重检查
                        cached_data = await cache.get(cache_key)
                        if cached_data is not None:
                            # 空值缓存处理
                            if cached_data == _NULL_CACHE_MARKER:
                                raise NotFoundException("资源不存在")

                            # Pydantic 模型反序列化
                            return_type = _get_return_type(func)
                            if return_type and hasattr(return_type, "model_validate"):
                                return return_type.model_validate(cached_data)

                            return cached_data

                        # 执行函数
                        result = await func(*args, **kwargs)

                        # 空值缓存处理：使用标记存储空值
                        if null_expire is not None and result is None:
                            await cache.set(cache_key, _NULL_CACHE_MARKER, expire=null_expire)
                            raise NotFoundException("资源不存在")

                        # 序列化结果（如果是 Pydantic 模型）
                        serialized = result.model_dump(mode="json") if isinstance(result, BaseModel) else result

                        await cache.set(cache_key, serialized, expire=expire, is_hot=is_hot)
                        return result
                    finally:
                        await cache.release_lock(cache_key)
                else:
                    logger.warning(f"获取锁失败，降级到数据库查询: {cache_key}")
                    # 获取锁失败，直接查询
                    result = await func(*args, **kwargs)
                    if null_expire is not None and result is None:
                        raise NotFoundException("资源不存在")
                    return result
            else:
                # 无锁模式
                result = await func(*args, **kwargs)

                # 空值缓存处理：使用标记存储空值
                if null_expire is not None and result is None:
                    await cache.set(cache_key, _NULL_CACHE_MARKER, expire=null_expire)
                    raise NotFoundException("资源不存在")

                # 序列化结果
                serialized = result.model_dump(mode="json") if isinstance(result, BaseModel) else result

                await cache.set(cache_key, serialized, expire=expire, is_hot=is_hot)
                return result

        return wrapper

    return decorator


__all__ = ["cached"]
