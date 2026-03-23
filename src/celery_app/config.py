# ============================================
# Celery 配置 - P9 WES Backend
# ============================================
# 用途: Beat 调度器配置和任务路由
# ============================================


# ============================================
# 定时任务配置 (Beat Schedule)
# ============================================

beat_schedule: dict[str, dict[str, str | float]] = {
    # ============================================
    # 健康检查任务
    # ============================================
    "health-check": {
        "task": "src.celery_app.tasks.core.health_check",
        "schedule": 60.0,  # 每分钟
    },
    # ============================================
    # 可扩展：添加新的定时任务
    # 示例：
    # "daily-inventory-check": {
    #     "task": "src.celery_app.tasks.inventory.check_low_stock",
    #     "schedule": 3600.0,  # 每小时
    # },
    # "daily-data-summary": {
    #     "task": "src.celery_app.tasks.reporting.generate_daily_summary",
    #     "schedule": 86400.0,  # 每天
    # },
}

# ============================================
# 任务路由配置
# ============================================

task_routes = {
    # 核心任务 -> default 队列
    "src.celery_app.tasks.core.*": {"queue": "default"},
    # 设备事件处理任务 -> device 队列
    "src.celery_app.tasks.device.*": {"queue": "device"},
    # 可扩展：添加新的任务路由
    # "src.celery_app.tasks.inventory.*": {"queue": "inventory"},
    # "src.celery_app.tasks.reporting.*": {"queue": "reporting"},
}
