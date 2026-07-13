# ============================================
# Celery 核心任务 - P9 WES Backend
# ============================================
# 用途: 系统级异步任务 (健康检查、缓存刷新等)
# ============================================

from typing import Any, TypedDict, cast

from celery import current_task  # pyright: ignore[reportMissingTypeStubs]
from sqlalchemy import text

from src.celery_app.app import celery_app
from src.celery_app.async_runtime import run_async
from src.core.logger import logger
from src.database.db import get_db_context
from src.database.redis_client import ensure_redis_connection, get_redis, is_redis_available
from src.utils.timezone import timezone


class CheckResult(TypedDict, total=False):
    status: str
    error: str
    db_size: int


class HealthCheckResult(TypedDict, total=False):
    status: str
    timestamp: str
    worker: str
    checks: dict[str, CheckResult]
    error: str


# ============================================
# 健康检查任务
# ============================================


def _update_health_cache(result: HealthCheckResult) -> None:
    """更新 API 层的健康状态缓存"""
    try:
        from src.core.health import system_health

        checks = result.get("checks", {})
        system_health.update(
            db_ok=checks.get("database", {}).get("status") == "connected",
            redis_ok=checks.get("redis", {}).get("status") == "connected",
            celery_ok=result.get("status") != "error",
        )
    except Exception as e:
        logger.warning(f"更新健康缓存失败: {e}")


@celery_app.task(name="src.celery_app.tasks.core.health_check")
def health_check() -> HealthCheckResult:
    """
    系统健康检查任务

    检查项：
    1. Worker 状态
    2. 数据库连接
    3. Redis 连接
    """
    try:
        logger.info("执行系统健康检查")

        hostname = "unknown"
        task = cast("Any", current_task)
        if task and task.request:
            hostname = cast("str", task.request.hostname)

        result: HealthCheckResult = {
            "status": "healthy",
            "timestamp": timezone.now_utc().isoformat(),
            "worker": hostname,
            "checks": {},
        }

        async def check_dependencies() -> tuple[CheckResult, CheckResult]:
            # 数据库和 Redis 共享统一 runtime，但数据库会话仅在本条消息内存活。
            try:
                async with get_db_context() as db:
                    _ = await db.execute(text("SELECT 1"))
                    db_status: CheckResult = {"status": "connected"}
            except Exception as e:
                db_status = {"status": "error", "error": str(e)}

            try:
                # Worker 启动时 Redis 可降级；健康检查通过标准入口按频率限制尝试恢复。
                _ = await ensure_redis_connection()
                if is_redis_available():
                    redis_client = cast("Any", get_redis())
                    if redis_client:
                        await redis_client.ping()
                        db_size = cast("int", await redis_client.dbsize())
                        redis_status: CheckResult = {
                            "status": "connected",
                            "db_size": db_size,
                        }
                    else:
                        redis_status = {"status": "unavailable"}
                else:
                    redis_status = {"status": "unavailable"}
            except Exception as e:
                redis_status = {"status": "error", "error": str(e)}
            return db_status, redis_status

        db_status, redis_status = run_async(check_dependencies)
        result["checks"]["database"] = db_status
        result["checks"]["redis"] = redis_status
        if db_status.get("status") != "connected" or redis_status.get("status") != "connected":
            result["status"] = "degraded"

        # 更新 API 层健康缓存
        _update_health_cache(result)

        logger.info(f"健康检查完成: {result['status']}")
        return result

    except Exception as e:
        logger.error(f"健康检查失败: {e}")
        return {
            "status": "unhealthy",
            "timestamp": timezone.now_utc().isoformat(),
            "error": str(e),
        }


# ============================================
# 缓存清除任务
# ============================================


@celery_app.task(name="src.celery_app.tasks.core.clear_cache")
def clear_cache(pattern: str = "app:*") -> dict[str, str | int]:
    """
    清除 Redis 缓存

    Args:
        pattern: Redis key 匹配模式，默认清除所有应用缓存 (app:*)

    返回:
        清除的键数量
    """
    try:
        logger.info(f"清除缓存: pattern={pattern}")

        async def _clear() -> dict[str, str | int]:
            if not is_redis_available():
                logger.warning("Redis 不可用，无法清除缓存")
                return {"status": "skipped", "reason": "Redis unavailable"}

            redis_client = cast("Any", get_redis())
            if redis_client is None:
                return {"status": "skipped", "reason": "Redis unavailable"}

            # 扫描匹配的键
            keys: list[str] = [cast("str", key) async for key in redis_client.scan_iter(match=pattern)]

            # 批量删除
            if keys:
                await redis_client.delete(*keys)

            return {
                "status": "success",
                "pattern": pattern,
                "cleared_count": len(keys),
            }

        result = run_async(_clear)
        if result.get("status") == "success":
            logger.info(f"缓存清除完成: 清除了 {result['cleared_count']} 个键")
        return result

    except Exception as e:
        logger.error(f"清除缓存失败: {e}")
        raise


# ============================================
# 通知发送任务
# ============================================


@celery_app.task(name="src.celery_app.tasks.core.send_notification")
def send_notification(user_id: int, message: str, notification_type: str = "info") -> dict[str, str | int]:
    """
    发送用户通知

    当前实现：记录到系统日志
    可扩展：WebSocket 推送、邮件、短信等

    Args:
        user_id: 用户 ID
        message: 通知消息内容
        notification_type: 通知类型 (info/warning/error/success)

    返回:
        发送结果
    """
    try:
        logger.info(f"发送通知: user_id={user_id}, type={notification_type}, message={message}")

        # 当前实现：记录到审计日志
        async def _save_notification() -> None:
            from src.app.sys.models.audit_log import OperaStatus
            from src.app.sys.services.audit_service import audit_log_service

            async with get_db_context() as db:
                # 映射通知类型到操作状态 (OperaStatus 只有 SUCCESS/FAIL)
                status = (
                    OperaStatus.SUCCESS if notification_type in ("info", "warning", "success") else OperaStatus.FAIL
                )

                audit_service = cast("Any", audit_log_service)
                _ = await audit_service.create_audit_log(
                    db=db,
                    method="NOTIFICATION",
                    title=f"系统通知 [{notification_type.upper()}]",
                    path=f"/user/{user_id}/notification",
                    args={"user_id": user_id, "message": message},
                    status=status,
                    code="200",
                    msg=message,
                )
                await db.commit()

        run_async(_save_notification)

        logger.info(f"通知发送成功: user_id={user_id}")

        return {
            "status": "success",
            "user_id": user_id,
            "notification_type": notification_type,
            "message": message,
        }

    except Exception as e:
        logger.error(f"发送通知失败: {e}")
        raise


# ============================================
# 日志清理任务
# ============================================


@celery_app.task(name="src.celery_app.tasks.core.cleanup_old_logs")
def cleanup_old_logs(days: int = 7) -> dict[str, str | int]:
    """
    清理旧的日志文件

    Args:
        days: 保留最近几天的日志，默认7天

    返回:
        清理的文件数量
    """
    from pathlib import Path

    try:
        logger.info(f"清理 {days} 天前的日志文件")

        log_dir = Path("logs")
        if not log_dir.exists():
            return {"status": "success", "cleaned": 0, "message": "日志目录不存在"}

        count = 0
        cutoff_time = timezone.now_utc().timestamp() - (days * 86400)

        for log_file in log_dir.glob("*.log"):
            if log_file.stat().st_mtime < cutoff_time:
                log_file.unlink()
                count += 1
                logger.debug(f"删除日志文件: {log_file.name}")

        logger.info(f"清理完成: 删除了 {count} 个日志文件")
        return {
            "status": "success",
            "cleaned": count,
            "days": days,
        }

    except Exception as e:
        logger.error(f"清理日志失败: {e}")
        raise


# ============================================
# 导出
# ============================================

__all__ = [
    "cleanup_old_logs",
    "clear_cache",
    "health_check",
    "process_signal",
    "send_notification",
]


@celery_app.task(
    name="src.celery_app.tasks.core.process_signal",
    base=celery_app.Task,
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def process_signal(self: Any, payload: dict[str, Any]) -> None:
    logger.info(f"core process_signal 接收到 payload: {payload}")
