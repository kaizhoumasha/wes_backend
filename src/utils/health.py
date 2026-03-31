"""
健康检查工具函数

提供统一的数据库和 Redis 健康检查功能，避免代码重复。
"""

import time
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logger import logger
from src.database.redis_client import ensure_redis_connection, get_redis, is_redis_available


async def check_database_health(db: AsyncSession) -> dict[str, Any]:
    """
    检查数据库健康状态

    Args:
        db: 数据库会话

    Returns:
        包含健康状态和响应时间的字典
    """
    try:
        start_time = time.time()
        _ = await db.execute(text("SELECT 1"))
        response_time = (time.time() - start_time) * 1000

        return {
            "status": "healthy",
            "response_time_ms": round(response_time, 2),
        }
    except Exception as e:
        logger.error(f"数据库健康检查失败: {e}")
        return {
            "status": "unhealthy",
            "error": str(e),
        }


async def check_redis_health() -> dict[str, Any]:
    """
    检查 Redis 健康状态

    Returns:
        包含健康状态、响应时间和连接信息的字典
    """
    try:
        # 尝试确保 Redis 连接（会自动重连）
        _ = await ensure_redis_connection()

        if not is_redis_available():
            return {
                "status": "degraded",
                "message": "Redis 不可用，应用以降级模式运行（系统会自动检测恢复）",
            }

        redis_client = get_redis()
        if redis_client is None:
            return {
                "status": "degraded",
                "message": "Redis 客户端未初始化",
            }

        # 测试连接并获取延迟
        start_time = time.time()
        await cast("Any", redis_client).ping()
        response_time = (time.time() - start_time) * 1000

        # 获取 Redis 信息
        info = await cast("Any", redis_client).info()
        db_size = await cast("Any", redis_client).dbsize()

        return {
            "status": "healthy",
            "connection_status": "connected",
            "response_time_ms": round(response_time, 2),
            "db_size": db_size,
            "used_memory": info.get("used_memory_human", "N/A"),
            "connected_clients": info.get("connected_clients", 0),
        }
    except Exception as e:
        logger.error(f"Redis 健康检查失败: {e}")
        return {
            "status": "error",
            "error": str(e),
        }


async def check_database_pool_status(db: AsyncSession) -> dict[str, Any] | None:
    """
    检查数据库连接池状态

    Args:
        db: 数据库会话

    Returns:
        包含连接池状态的字典，如果无法获取则返回 None
    """
    try:
        from src.database.db import engine

        if engine is None:
            return None

        pool = cast("Any", engine.pool)
        return {
            "pool_size": pool.size(),
            "checked_out": pool.checkedout(),
        }
    except Exception:
        return None


__all__ = [
    "check_database_health",
    "check_database_pool_status",
    "check_redis_health",
]
