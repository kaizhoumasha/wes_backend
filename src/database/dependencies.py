"""
FastAPI 依赖注入定义

支持自动降级的依赖类型
"""

from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis

from .db import AsyncSession, get_db
from .redis_cache import RedisCache, get_cache
from .redis_client import get_redis

# 数据库依赖
AsyncSessionDep = Annotated[AsyncSession, Depends(get_db)]


def _get_redis_safe() -> Redis | None:
    """
    安全获取 Redis 客户端（支持降级）

    如果 Redis 不可用，返回 None 而不是抛出异常
    """
    try:
        return get_redis()
    except Exception:
        return None


# Redis 原生客户端依赖（可选）
# ⚠️ 注意：可能为 None，使用前需要检查
RedisDep = Annotated[Redis | None, Depends(_get_redis_safe)]


def _get_cache_service() -> RedisCache:
    """
    获取缓存服务（支持自动降级）

    推荐使用此依赖而不是 RedisDep，因为它提供完整的缓存功能
    和自动降级支持
    """
    return get_cache()


# 缓存服务依赖（推荐）
# ✅ 自动处理降级、序列化、熔断等
CacheDep = Annotated[RedisCache, Depends(_get_cache_service)]


__all__ = [
    "AsyncSessionDep",
    "CacheDep",
    "RedisDep",
]
