# ============================================
# Celery 配置 - P9 WES Backend
# ============================================
# 用途: Beat 调度器配置和任务路由
# ============================================


# ============================================
# 定时任务配置 (Beat Schedule)
# ============================================

beat_schedule = {
    # ============================================
    # 健康检查任务
    # ============================================
    "health-check": {
        "task": "src.celery_app.tasks.core.health_check",
        "schedule": 60.0,  # 每分钟
    },
    # ============================================
    # TODO: 未来添加的任务
    # ============================================
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
    # TODO: 未来添加的路由
    # "src.celery_app.tasks.inventory.*": {"queue": "inventory"},
    # "src.celery_app.tasks.reporting.*": {"queue": "reporting"},
}
