# ============================================
# Celery 配置 - P9 WES Backend
# ============================================
# 用途: Beat 调度器配置和任务路由
# ============================================

from typing import Any

# ============================================
# 定时任务配置 (Beat Schedule)
# ============================================
# 注意：inbox/outbox 处理已支持事件驱动即时触发，
#       Beat 仅作兜底轮询，无需高频调度。
# ============================================

beat_schedule: dict[str, dict[str, Any]] = {
    # 健康检查任务
    "health-check": {
        "task": "src.celery_app.tasks.core.health_check",
        "schedule": 60.0,  # 每分钟
    },
    # Inbox 消息处理 - 扫描并处理新消息（兜底）
    # 正常流程由 API 写入 Inbox 后即时 send_task 触发，Beat 仅处理遗漏/重试
    "process-runtime-inbox-batch": {
        "task": "src.celery_app.tasks.runtime_inbox.process_runtime_inbox_batch",
        "schedule": 10.0,  # 兜底轮询（原 1s，优化后 10s）
    },
    # SystemOutbox 消息派发 - 统一处理面向外部硬件系统的副作用（兜底）
    # 正常流程由编排完成后即时 send_task 触发，Beat 仅处理遗漏/重试
    "dispatch-outbox-batch": {
        "task": "src.celery_app.tasks.sys.dispatch_system_outbox_batch",
        "schedule": 10.0,  # 兜底轮询（原 1s，优化后 10s）
    },
    # WMS data EFFECT Outbox - WES 通用 worker 实际拥有的 lane（兜底）
    "dispatch-wms-data-outbox-batch": {
        "task": "src.celery_app.tasks.sys.dispatch_wms_data_outbox_batch",
        "schedule": 10.0,
    },
    # WMS fulfillment EFFECT Outbox - 只路由到专用 worker（兜底）
    "dispatch-wms-fulfillment-outbox-batch": {
        "task": "src.celery_app.tasks.sys.dispatch_wms_fulfillment_outbox_batch",
        "schedule": 10.0,
        "options": {"expires": 10.0},
    },
    # WMS EFFECT 状态确认 - 即时任务由 dispatch key 触发，Beat 仅扫描遗漏/到期项。
    "scan-wms-effect-status-batch": {
        "task": "src.celery_app.tasks.workline.scan_wms_effect_status_batch",
        "schedule": 10.0,
        "options": {"expires": 10.0},
    },
    # Transport task/evidence/reconcile 均只唤醒数据库扫描，过期消息由下一周期替代。
    "submit-transport-tasks-batch": {
        "task": "src.celery_app.tasks.transport.submit_transport_tasks_batch",
        "schedule": 30.0,
        "kwargs": {"limit": 100},
        "options": {"expires": 30.0},
    },
    "process-transport-evidence-batch": {
        "task": "src.celery_app.tasks.transport.process_transport_evidence_batch",
        "schedule": 10.0,
        "kwargs": {"limit": 100},
        "options": {"expires": 10.0},
    },
    "reconcile-transport-tasks-batch": {
        "task": "src.celery_app.tasks.transport.reconcile_transport_tasks_batch",
        "schedule": 30.0,
        "kwargs": {"limit": 100},
        "options": {"expires": 30.0},
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
}

# ============================================
# 任务路由配置
# ============================================

task_routes = {
    # 三个 Outbox dispatcher 必须直达静态队列，不能依赖 sys.* 通配路由推断 lane。
    "src.celery_app.tasks.sys.dispatch_system_outbox_batch": {"queue": "celery"},
    "src.celery_app.tasks.sys.dispatch_wms_data_outbox_batch": {"queue": "celery"},
    "src.celery_app.tasks.sys.dispatch_wms_fulfillment_outbox_batch": {"queue": "wms-fulfillment"},
    # WMS async EFFECT 状态查询复用 fulfillment worker 的 actual lane client。
    "src.celery_app.tasks.workline.check_wms_effect_status": {"queue": "wms-fulfillment"},
    "src.celery_app.tasks.workline.scan_wms_effect_status_batch": {"queue": "wms-fulfillment"},
    "src.celery_app.tasks.transport.submit_transport_tasks_batch": {"queue": "wms-fulfillment"},
    "src.celery_app.tasks.transport.process_transport_evidence_batch": {"queue": "wms-fulfillment"},
    "src.celery_app.tasks.transport.reconcile_transport_tasks_batch": {"queue": "wms-fulfillment"},
    # 核心任务 -> default 队列
    "src.celery_app.tasks.core.*": {"queue": "default"},
    # RuntimeInbox 主链路任务 -> celery 队列
    "src.celery_app.tasks.runtime_inbox.*": {"queue": "celery"},
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
