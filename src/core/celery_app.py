# ============================================
# Celery 应用配置 - P9 WES Backend
# ============================================
# 用途: 异步任务处理和定时任务调度
# ============================================

from celery import Celery
from celery.schedules import crontab
from loguru import logger

from src.core.conf import settings

# ============================================
# Celery 应用实例
# ============================================

celery_app = Celery(
    "wes_backend",
    broker=settings.CELERY_BROKER,
    backend=settings.CELERY_BACKEND,
    include=[
        "src.app.warehousing.tasks",  # 入库任务
        "src.app.inventory.tasks",     # 库存任务
        "src.app.reporting.tasks",     # 报表任务
    ],
)

# ============================================
# Celery 配置
# ============================================

celery_app.conf.update(
    # ================================
    # 任务配置
    # ================================
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,

    # ================================
    # 任务执行配置
    # ================================
    task_always_eager=False,  # 生产环境设为 False
    task_eager_propagates=True,
    task_ignore_result=True,  # 不保存结果 (减少 Redis 内存使用)

    # ================================
    # 任务路由
    # ================================
    task_routes={
        # 入库相关任务 -> warehousing 队列
        "src.app.warehousing.tasks.*": {"queue": "warehousing"},
        # 库存相关任务 -> inventory 队列
        "src.app.inventory.tasks.*": {"queue": "inventory"},
        # 报表相关任务 -> reporting 队列
        "src.app.reporting.tasks.*": {"queue": "reporting"},
    },

    # ================================
    # 任务重试配置
    # ================================
    task_acks_late=True,  # 任务执行后才确认 (防止任务丢失)
    worker_prefetch_multiplier=4,  # 预取任务数
    task_max_retries=3,
    task_default_retry_delay=60,  # 重试延迟 (秒)

    # ================================
    # 结果后端配置
    # ================================
    result_expires=3600,  # 结果保留时间 (秒)
    result_compression="gzip",  # 压缩结果

    # ================================
    # Worker 配置
    # ================================
    worker_concurrency=4,  # 并发任务数 (默认: CPU 核心数)
    worker_max_tasks_per_child=1000,  # 每个 Worker 处理的最大任务数
    worker_log_format="[%(asctime)s: %(levelname)s/%(processName)s] %(message)s",
    worker_task_log_format="[%(asctime)s: %(levelname)s/%(processName)s][%(task_name)s(%(task_id)s)] %(message)s",

    # ================================
    # Beat 调度器配置 (定时任务)
    # ================================
    beat_scheduler_filename="celerybeat-schedule",
    beat_log_format="[%(asctime)s: %(levelname)s] %(message)s",
    beat_max_loop_interval=5.0,  # Beat 循环间隔 (秒)

    # ================================
    # 安全配置
    # ================================
    broker_connection_retry_on_startup=True,  # 启动时重试连接
    broker_connection_retry=True,  # 自动重试连接
    broker_connection_max_retries=10,  # 最大重试次数
)

# ============================================
# 定时任务配置 (Beat Schedule)
# ============================================

celery_app.conf.beat_schedule = {
    # ============================================
    # 每日任务
    # ============================================
    "daily-inventory-check": {
        "task": "src.app.inventory.tasks.check_low_stock",
        "schedule": 3600.0,  # 每小时执行一次
        # "schedule": crontab(hour=2, minute=0),  # 每天凌晨 2 点
    },

    "daily-data-summary": {
        "task": "src.app.reporting.tasks.generate_daily_summary",
        "schedule": 86400.0,  # 每天执行一次
        # "schedule": crontab(hour=3, minute=0),  # 每天凌晨 3 点
    },

    # ============================================
    # 每周任务
    # ============================================
    "weekly-performance-report": {
        "task": "src.app.reporting.tasks.generate_weekly_report",
        "schedule": crontab(hour=4, minute=0, day_of_week=1),  # 每周一凌晨 4 点
    },

    # ============================================
    # 每月任务
    # ============================================
    "monthly-data-cleanup": {
        "task": "src.app.warehousing.tasks.cleanup_old_records",
        "schedule": crontab(hour=5, minute=0, day_of_month=1),  # 每月 1 号凌晨 5 点
    },

    # ============================================
    # 高频任务
    # ============================================
    "cache-refresh": {
        "task": "src.app.inventory.tasks.refresh_cache",
        "schedule": 300.0,  # 每 5 分钟
    },

    "health-check": {
        "task": "src.core.celery_tasks.health_check",
        "schedule": 60.0,  # 每分钟
    },
}

# ============================================
# 调试任务
# ============================================

@celery_app.task(name="src.core.celery_app.debug_task")
def debug_task():
    """调试任务 - 用于测试 Celery 是否正常工作"""
    print("Celery 任务执行成功!")
    return {"status": "success", "message": "Celery is working!"}

# ============================================
# 导出
# ============================================

__all__ = ["celery_app"]
