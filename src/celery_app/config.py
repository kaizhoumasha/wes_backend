# ============================================
# Celery 配置 - P9 WES Backend
# ============================================
# 用途: Beat 调度器配置和任务路由
# ============================================


# ============================================
# 定时任务配置 (Beat Schedule)
# ============================================
# 注意：inbox/outbox 处理已支持事件驱动即时触发，
#       Beat 仅作兜底轮询，无需高频调度。
# ============================================

beat_schedule: dict[str, dict[str, str | float]] = {
    # 健康检查任务
    "health-check": {
        "task": "src.celery_app.tasks.core.health_check",
        "schedule": 60.0,  # 每分钟
    },
    # Inbox 消息处理 - 扫描并处理新消息（兜底）
    # 正常流程由 API 写入 Inbox 后即时 send_task 触发，Beat 仅处理遗漏/重试
    "process-inbox-batch": {
        "task": "src.celery_app.tasks.workline.process_inbox_batch",
        "schedule": 10.0,  # 兜底轮询（原 1s，优化后 10s）
    },
    # SystemOutbox 消息派发 - 统一处理面向外部硬件系统的副作用（兜底）
    # 正常流程由编排完成后即时 send_task 触发，Beat 仅处理遗漏/重试
    "dispatch-outbox-batch": {
        "task": "src.celery_app.tasks.sys.dispatch_system_outbox_batch",
        "schedule": 10.0,  # 兜底轮询（原 1s，优化后 10s）
    },
    # 超时 Session 扫描任务
    "scan-timeouts-batch": {
        "task": "src.celery_app.tasks.workline.scan_timeouts_batch",
        "schedule": 30.0,  # 每30秒扫描一次
    },
    # 设备心跳超时扫描任务
    "scan-device-heartbeats-batch": {
        "task": "src.celery_app.tasks.workline.scan_device_heartbeats_batch",
        "schedule": 300.0,  # 每 5 分钟扫描一次
    },
    # SMT 入库 handoff 兜底恢复 - 扫描到期 demand 和 claim 后卡住的 source item
    "scan-smt-inbound-handoff-demands-batch": {
        "task": "src.celery_app.tasks.workline.scan_smt_inbound_handoff_demands_batch",
        "schedule": 30.0,
    },
}

# ============================================
# 任务路由配置
# ============================================

task_routes = {
    # 核心任务 -> default 队列
    "src.celery_app.tasks.core.*": {"queue": "default"},
    # 作业线编排任务 -> celery 队列
    "src.celery_app.tasks.workline.*": {"queue": "celery"},
    # 系统级 Handling 任务 -> celery 队列
    "src.celery_app.tasks.handling.*": {"queue": "celery"},
    # 系统级任务 -> celery 队列
    "src.celery_app.tasks.sys.*": {"queue": "celery"},
    # 可扩展：添加新的任务路由
    # "src.celery_app.tasks.inventory.*": {"queue": "inventory"},
    # "src.celery_app.tasks.reporting.*": {"queue": "reporting"},
}
