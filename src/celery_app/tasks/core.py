# ============================================
# Celery 核心任务 - P9 WES Backend
# ============================================
# 用途: 系统级异步任务 (健康检查、缓存刷新等)
# ============================================

from celery import current_app
from loguru import logger

from src.celery_app.app import celery_app
from src.utils.timezone import timezone


@celery_app.task(name="celery_app.tasks.core.health_check")
def health_check():
    """健康检查任务"""
    try:
        logger.info("执行健康检查任务")

        # TODO: 添加实际的数据库和 Redis 健康检查
        # from src.database.db import async_engine
        # from src.core.conf import settings

        return {
            "status": "healthy",
            "timestamp": timezone.now_utc().isoformat(),
            "worker": current_app.current_worker_task.request.hostname,
        }
    except Exception as e:
        logger.error(f"健康检查失败: {e}")
        return {
            "status": "unhealthy",
            "timestamp": timezone.now_utc().isoformat(),
            "error": str(e),
        }


@celery_app.task(name="celery_app.tasks.core.clear_cache")
def clear_cache(pattern: str = "*"):
    """清除缓存"""
    try:
        logger.info(f"清除缓存: pattern={pattern}")

        # TODO: 添加实际的缓存清除逻辑
        # redis_client.keys(pattern) -> delete

        return {"status": "success", "cleared": pattern}
    except Exception as e:
        logger.error(f"清除缓存失败: {e}")
        raise


@celery_app.task(name="celery_app.tasks.core.send_notification")
def send_notification(user_id: int, message: str, notification_type: str = "info"):
    """发送通知"""
    try:
        logger.info(f"发送通知: user_id={user_id}, type={notification_type}")

        # TODO: 添加实际的通知发送逻辑
        # WebSocket、邮件、短信等

        return {"status": "success", "user_id": user_id}
    except Exception as e:
        logger.error(f"发送通知失败: {e}")
        raise


@celery_app.task(name="celery_app.tasks.core.cleanup_old_logs")
def cleanup_old_logs(days: int = 7):
    """清理旧日志文件"""
    from pathlib import Path

    try:
        logger.info(f"清理 {days} 天前的日志文件")

        log_dir = Path("logs")
        if not log_dir.exists():
            return {"status": "success", "cleaned": 0}

        count = 0
        cutoff_time = timezone.now_utc().timestamp() - (days * 86400)

        for log_file in log_dir.glob("*.log"):
            if log_file.stat().st_mtime < cutoff_time:
                log_file.unlink()
                count += 1

        logger.info(f"清理了 {count} 个日志文件")
        return {"status": "success", "cleaned": count}
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
    "send_notification",
]
