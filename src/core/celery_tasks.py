# ============================================
# Celery 系统任务 - P9 WES Backend
# ============================================
# 用途: 系统级异步任务 (健康检查、缓存刷新等)
# ============================================

from datetime import datetime
from celery import current_app
from loguru import logger

from src.core.celery_app import celery_app


@celery_app.task(name="src.core.celery_tasks.health_check")
def health_check():
    """健康检查任务"""
    try:
        logger.info("执行健康检查任务")

        # 检查数据库连接
        from src.database.db import async_engine
        # 这里可以添加实际的数据库健康检查逻辑

        # 检查 Redis 连接
        from src.core.conf import settings
        # 这里可以添加实际的 Redis 健康检查逻辑

        return {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "worker": current_app.current_worker_task.request.hostname,
        }
    except Exception as e:
        logger.error(f"健康检查失败: {e}")
        return {
            "status": "unhealthy",
            "timestamp": datetime.now().isoformat(),
            "error": str(e),
        }


@celery_app.task(name="src.core.celery_tasks.clear_cache")
def clear_cache(pattern: str = "*"):
    """清除缓存"""
    try:
        logger.info(f"清除缓存: pattern={pattern}")

        # 这里添加实际的缓存清除逻辑
        # 例如: redis_client.keys(pattern) -> delete

        return {"status": "success", "cleared": pattern}
    except Exception as e:
        logger.error(f"清除缓存失败: {e}")
        raise


@celery_app.task(name="src.core.celery_tasks.send_notification")
def send_notification(user_id: int, message: str, notification_type: str = "info"):
    """发送通知"""
    try:
        logger.info(f"发送通知: user_id={user_id}, type={notification_type}")

        # 这里添加实际的通知发送逻辑
        # 例如: WebSocket、邮件、短信等

        return {"status": "success", "user_id": user_id}
    except Exception as e:
        logger.error(f"发送通知失败: {e}")
        raise


@celery_app.task(name="src.core.celery_tasks.cleanup_old_logs")
def cleanup_old_logs(days: int = 7):
    """清理旧日志文件"""
    import os
    from pathlib import Path

    try:
        logger.info(f"清理 {days} 天前的日志文件")

        log_dir = Path("logs")
        if not log_dir.exists():
            return {"status": "success", "cleaned": 0}

        count = 0
        cutoff_time = datetime.now().timestamp() - (days * 86400)

        for log_file in log_dir.glob("*.log"):
            if log_file.stat().st_mtime < cutoff_time:
                log_file.unlink()
                count += 1

        logger.info(f"清理了 {count} 个日志文件")
        return {"status": "success", "cleaned": count}
    except Exception as e:
        logger.error(f"清理日志失败: {e}")
        raise


__all__ = [
    "health_check",
    "clear_cache",
    "send_notification",
    "cleanup_old_logs",
]
