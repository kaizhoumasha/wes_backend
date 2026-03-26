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
    # Inbox 消息处理任务 - 定期扫描并处理新消息
    # ============================================
    "process-inbox-batch": {
        "task": "src.celery_app.tasks.workline.process_inbox_batch",
        "schedule": 5.0,  # 每5秒处理一次
    },
    # ============================================
    # Outbox 消息派发任务 - 定期将命令下发给设备
    # ============================================
    "dispatch-outbox-batch": {
        "task": "src.celery_app.tasks.workline.dispatch_outbox_batch",
        "schedule": 5.0,  # 每5秒派发一次
    },
    # ============================================
    # 超时 Session 扫描任务
    # ============================================
    "scan-timeouts-batch": {
        "task": "src.celery_app.tasks.workline.scan_timeouts_batch",
        "schedule": 30.0,  # 每30秒扫描一次
    },
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
