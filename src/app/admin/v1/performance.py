"""
性能测试 API 端点

用于监控系统性能指标，包括：
1. CPU 和内存使用情况
2. 数据库连接池状态
3. Redis 连接状态
4. 响应时间统计
5. 并发请求数
"""

import time
from typing import Any

import psutil
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.conf import settings
from src.core.logger import logger
from src.database.db import get_db
from src.database.redis_cache import get_cache
from src.database.redis_client import get_redis


def get_engine():
    """获取数据库引擎"""
    from src.database.db import engine

    if engine is None:
        raise RuntimeError("数据库引擎未初始化")
    return engine


router = APIRouter(prefix="/performance", tags=["性能监控"])


@router.get("/metrics", summary="获取系统性能指标")
async def get_performance_metrics(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """
    获取系统性能指标

    返回：
    - system: CPU、内存、磁盘使用情况
    - database: 数据库连接池状态
    - redis: Redis 连接状态
    - cache: 缓存统计信息
    """
    # 系统指标
    cpu_percent = psutil.cpu_percent(interval=0.1)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    system_metrics = {
        "cpu": {
            "percent": cpu_percent,
            "count": psutil.cpu_count(),
        },
        "memory": {
            "total": memory.total,
            "available": memory.available,
            "percent": memory.percent,
            "used": memory.used,
        },
        "disk": {
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
            "percent": disk.percent,
        },
    }

    # 数据库指标
    try:
        # 测试数据库连接
        start_time = time.time()
        await db.execute(text("SELECT 1"))
        db_response_time = (time.time() - start_time) * 1000

        # 获取引擎并检查连接池状态
        engine = get_engine()
        pool = engine.pool
        db_metrics = {
            "status": "connected",
            "response_time_ms": round(db_response_time, 2),
            "pool_size": pool.size(),
            "checked_out": pool.checkedout(),
        }
    except Exception as e:
        logger.error(f"数据库健康检查失败: {e}")
        db_metrics = {
            "status": "error",
            "error": str(e),
        }

    # Redis 指标
    try:
        from src.database.redis_client import ensure_redis_connection, is_redis_available

        # 尝试确保 Redis 连接（会自动重连）
        await ensure_redis_connection()

        if is_redis_available():
            redis_client = get_redis()
            if redis_client is not None:
                start_time = time.time()
                await redis_client.ping()
                redis_response_time = (time.time() - start_time) * 1000

                # 获取 Redis 信息
                info = await redis_client.info()
                db_size = await redis_client.dbsize()

                redis_metrics = {
                    "status": "connected",
                    "response_time_ms": round(redis_response_time, 2),
                    "db_size": db_size,
                    "used_memory": info.get("used_memory_human", "N/A"),
                    "connected_clients": info.get("connected_clients", 0),
                }
            else:
                redis_metrics = {"status": "degraded", "message": "Redis 客户端未初始化"}
        else:
            redis_metrics = {
                "status": "degraded",
                "message": "Redis 不可用，应用以降级模式运行（系统会自动检测恢复）",
            }
    except Exception as e:
        logger.error(f"Redis 健康检查失败: {e}")
        redis_metrics = {
            "status": "error",
            "error": str(e),
        }

    # 缓存指标
    try:
        cache = get_cache()
        cache_status = cache.get_status()
        cache_metrics = {
            "status": "active" if cache_status["available"] else "degraded",
            "prefix": cache.prefix,
            "circuit_breaker": {
                "state": cache_status["circuit_breaker_state"],
                "failure_count": cache_status["failure_count"],
                "failure_threshold": cache_status["failure_threshold"],
            },
            "redis_available": cache.redis is not None,
        }
    except Exception as e:
        cache_metrics = {
            "status": "error",
            "error": str(e),
            "redis_available": False,
        }

    return {
        "timestamp": time.time(),
        "system": system_metrics,
        "database": db_metrics,
        "redis": redis_metrics,
        "cache": cache_metrics,
    }


@router.get("/health", summary="健康检查")
async def health_check(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """
    简单健康检查

    返回各组件的健康状态
    """
    health_status = {
        "status": "healthy",
        "timestamp": time.time(),
        "components": {},
    }

    # 检查数据库
    try:
        await db.execute(text("SELECT 1"))
        health_status["components"]["database"] = {
            "status": "healthy",
        }
    except Exception as e:
        health_status["status"] = "unhealthy"
        health_status["components"]["database"] = {
            "status": "unhealthy",
            "error": str(e),
        }

    # 检查 Redis
    try:
        redis_client = get_redis()
        await redis_client.ping()
        health_status["components"]["redis"] = {
            "status": "healthy",
        }
    except Exception as e:
        health_status["status"] = "unhealthy"
        health_status["components"]["redis"] = {
            "status": "unhealthy",
            "error": str(e),
        }

    return health_status


@router.post("/load-test/reset", summary="重置性能测试数据")
async def reset_load_test_data(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """
    重置性能测试数据

    清空所有缓存，准备开始新的性能测试
    """
    try:
        # 清空 Redis 缓存
        cache = get_cache()
        await cache.delete_pattern("*")

        logger.info("性能测试数据已重置")
        return {
            "status": "success",
            "message": "性能测试数据已重置",
        }
    except Exception as e:
        logger.error(f"重置性能测试数据失败: {e}")
        return {
            "status": "error",
            "error": str(e),
        }


@router.get("/config", summary="获取性能测试配置")
async def get_performance_config() -> dict[str, Any]:
    """
    获取系统配置信息

    用于性能测试时了解系统配置
    """
    return {
        "app": {
            "name": settings.PROJECT_NAME,
            "version": settings.VERSION,
            "debug": settings.APP_DEBUG,
        },
        "database": {
            "url": str(settings.DATABASE_URL).split("@")[-1]
            if "@" in str(settings.DATABASE_URL)
            else "configured",
        },
        "redis": {
            "url": str(settings.REDIS_URL).split("@")[-1]
            if "@" in str(settings.REDIS_URL)
            else "configured",
        },
        "performance": {
            "db_pool_size": 20,
            "db_max_overflow": 10,
            "redis_max_connections": 50,
        },
    }
