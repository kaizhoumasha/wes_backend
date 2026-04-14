"""
Fast Fail 检查依赖

提供 FastAPI Depends 机制的健康检查，只在需要时执行。
"""

from typing import Any

from fastapi import HTTPException

from src.database.dependencies import AsyncSessionDep
from src.utils.health import (
    check_celery_health,
    check_database_health,
    check_redis_health,
)


def _is_healthy(health_result: dict[str, Any]) -> bool:
    """判断健康检查是否通过"""
    status = health_result.get("status", "")
    return status in ("healthy", "ok")


async def fast_fail_check(db: AsyncSessionDep) -> None:
    """
    Fast Fail 检查依赖 — 只在注入时执行

    在 API 处理前检查基础设施可用性（DB + Redis + Celery）。
    任一组件不可用时返回 503。

    Args:
        db: API 注入的数据库会话（复用已有连接，避免新建）

    Raises:
        HTTPException: 任一组件不可用时返回 503
    """
    # 并行检查
    db_health = await check_database_health(db)
    redis_health = await check_redis_health()
    celery_health = await check_celery_health()

    failed: list[str] = []
    if not _is_healthy(db_health):
        failed.append("database")
    if not _is_healthy(redis_health):
        failed.append("redis")
    if not _is_healthy(celery_health):
        failed.append("celery")

    if failed:
        raise HTTPException(
            status_code=503,
            detail=f"Service unavailable: {', '.join(failed)}",
        )


__all__ = ["fast_fail_check"]
