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
from typing import Any, cast

import psutil
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.conf import settings
from src.core.exceptions import PermissionException
from src.core.logger import logger
from src.core.security import require_auth
from src.database.db import get_db
from src.database.redis_cache import get_cache
from src.utils.health import check_database_health, check_database_pool_status, check_redis_health

router = APIRouter(prefix="/performance", tags=["性能监控"], dependencies=[Depends(require_auth)])


@router.get("/metrics", summary="获取系统性能指标")
async def get_performance_metrics(
    db: AsyncSession = Depends(get_db),  # pyright: ignore[reportCallInDefaultInitializer]
) -> dict[str, Any]:
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

    system_metrics: dict[str, Any] = {
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
    db_health = await check_database_health(db)
    if db_health["status"] == "healthy":
        # 获取连接池状态
        pool_status = await check_database_pool_status(db)
        if pool_status:
            db_metrics: dict[str, Any] = {
                "status": "connected",
                "response_time_ms": db_health["response_time_ms"],
                **pool_status,
            }
        else:
            db_metrics = {
                "status": "connected",
                "response_time_ms": db_health["response_time_ms"],
            }
    else:
        db_metrics = cast("dict[str, Any]", db_health)

    # Redis 指标
    redis_metrics = await check_redis_health()

    # 缓存指标
    try:
        cache = cast("Any", get_cache())
        cache_status = cast("dict[str, Any]", cache.get_status())
        cache_metrics: dict[str, Any] = {
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
async def health_check(
    db: AsyncSession = Depends(get_db),  # pyright: ignore[reportCallInDefaultInitializer]
) -> dict[str, Any]:
    """
    简单健康检查

    返回各组件的健康状态
    """
    overall_status = "healthy"
    components = {}

    # 检查数据库
    db_health = await check_database_health(db)
    components["database"] = db_health
    if db_health["status"] != "healthy":
        overall_status = "unhealthy"

    # 检查 Redis
    redis_health = await check_redis_health()
    components["redis"] = redis_health
    if redis_health["status"] not in ("healthy", "degraded"):
        overall_status = "unhealthy"

    return {
        "status": overall_status,
        "timestamp": time.time(),
        "components": components,
    }


@router.post("/load-test/reset", summary="重置性能测试数据")
async def reset_load_test_data(
    db: AsyncSession = Depends(get_db),  # pyright: ignore[reportCallInDefaultInitializer]
) -> dict[str, Any]:
    """
    重置性能测试数据

    清空所有缓存，准备开始新的性能测试
    """
    if not settings.APP_DEBUG:
        raise PermissionException("该接口仅在开发/测试环境开放")

    try:
        # 清空 Redis 缓存
        cache = get_cache()
        _ = await cache.delete_pattern("*")

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
            "url": str(settings.DATABASE_URL).split("@")[-1] if "@" in str(settings.DATABASE_URL) else "configured",
        },
        "redis": {
            "url": str(settings.REDIS_URL).split("@")[-1] if "@" in str(settings.REDIS_URL) else "configured",
        },
        "performance": {
            "transaction_boundary": "service-managed",
            "database_pool": "runtime-configured",
            "redis_pool": "runtime-configured",
        },
    }
