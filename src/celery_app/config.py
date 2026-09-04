# ============================================
# Celery 配置 - P9 WES Backend
# ============================================
# 用途: Beat 调度器配置和任务路由
# ============================================

from typing import Any

# ============================================
# 定时任务配置 (Beat Schedule)
# ============================================
# target reliable-object scanners 仅作有界兜底轮询。
# ============================================

beat_schedule: dict[str, dict[str, Any]] = {
    # 健康检查任务
    "health-check": {
        "task": "src.celery_app.tasks.core.health_check",
        "schedule": 60.0,  # 每分钟
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
    "publish-transport-outcomes-batch": {
        "task": "src.celery_app.tasks.transport.publish_transport_outcomes_batch",
        "schedule": 10.0,
        "kwargs": {"limit": 100},
        "options": {"expires": 10.0},
    },
    "advance-transport-debug-runs-batch": {
        "task": "src.celery_app.tasks.transport.advance_transport_debug_runs_batch",
        "schedule": 10.0,
        "kwargs": {"limit": 100},
        "options": {"expires": 10.0},
    },
    # DeviceCommand 三类任务只携带扫描上限，不携带命令快照。
    "dispatch-device-commands-batch": {
        "task": "src.celery_app.tasks.device_command.dispatch_device_commands_batch",
        "schedule": 10.0,
        "kwargs": {"limit": 100},
        "options": {"expires": 10.0},
    },
    "process-device-evidence-batch": {
        "task": "src.celery_app.tasks.device_command.process_device_evidence_batch",
        "schedule": 10.0,
        "kwargs": {"limit": 100},
        "options": {"expires": 10.0},
    },
    "reconcile-device-commands-batch": {
        "task": "src.celery_app.tasks.device_command.reconcile_device_commands_batch",
        "schedule": 30.0,
        "kwargs": {"limit": 100},
        "options": {"expires": 30.0},
    },
    "process-execution-facts-batch": {
        "task": "src.celery_app.tasks.execution.process_execution_facts_batch",
        "schedule": 10.0,
        "kwargs": {"limit": 100},
        "options": {"expires": 10.0},
    },
    "dispatch-wms-confirmations-batch": {
        "task": "src.celery_app.tasks.wms_confirmation.dispatch_wms_confirmations_batch",
        "schedule": 10.0,
        "kwargs": {"limit": 100},
        "options": {"expires": 10.0},
    },
    "drain-safety-incidents-batch": {
        "task": "src.celery_app.tasks.workline.drain_safety_incidents_batch",
        "schedule": 10.0,
        "kwargs": {"limit": 10, "command_limit": 100},
        "options": {"expires": 10.0},
    },
}

# ============================================
# 任务路由配置
# ============================================

task_routes = {
    "src.celery_app.tasks.workline.drain_safety_incidents_batch": {"queue": "celery"},
    "src.celery_app.tasks.wms_confirmation.dispatch_wms_confirmations_batch": {"queue": "wms-fulfillment"},
    "src.celery_app.tasks.execution.process_execution_facts_batch": {"queue": "device-command"},
    "src.celery_app.tasks.device_command.dispatch_device_commands_batch": {"queue": "device-command"},
    "src.celery_app.tasks.device_command.process_device_evidence_batch": {"queue": "device-command"},
    "src.celery_app.tasks.device_command.reconcile_device_commands_batch": {"queue": "device-command"},
    "src.celery_app.tasks.transport.submit_transport_tasks_batch": {"queue": "wms-fulfillment"},
    "src.celery_app.tasks.transport.process_transport_evidence_batch": {"queue": "wms-fulfillment"},
    "src.celery_app.tasks.transport.reconcile_transport_tasks_batch": {"queue": "wms-fulfillment"},
    "src.celery_app.tasks.transport.publish_transport_outcomes_batch": {"queue": "wms-fulfillment"},
    "src.celery_app.tasks.transport.advance_transport_debug_runs_batch": {"queue": "wms-fulfillment"},
    # 核心任务 -> default 队列
    "src.celery_app.tasks.core.*": {"queue": "default"},
}
