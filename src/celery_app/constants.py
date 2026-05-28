"""
Celery 任务常量定义

定义作业线编排 Celery 任务的运行时常量。
"""

# Workline 业务默认值保留兼容导出；定义归属在应用服务层，避免 Service 反向依赖 celery_app。
from src.app.workline.constants import (
    DEFAULT_COMMAND_PRIORITY,
    DEFAULT_COMMAND_TIMEOUT_MS,
    EXTERNAL_HTTP_DECISION_TYPE,
    EXTERNAL_HTTP_INBOX_KIND,
    INBOX_PROCESS_TIMEOUT_SECONDS,
)

# 设备心跳超时（秒）
# 仅对已有 last_heartbeat_at 的 IDLE/RUNNING 设备生效，避免把未启用心跳的设备误判离线
DEVICE_HEARTBEAT_TIMEOUT_SECONDS = 120

# Outbox 元数据字段
OUTBOX_META_FIELDS = ("command_code", "task_type", "priority", "timeout", "timestamp")

__all__ = [
    "DEFAULT_COMMAND_PRIORITY",
    "DEFAULT_COMMAND_TIMEOUT_MS",
    "DEVICE_HEARTBEAT_TIMEOUT_SECONDS",
    "EXTERNAL_HTTP_DECISION_TYPE",
    "EXTERNAL_HTTP_INBOX_KIND",
    "INBOX_PROCESS_TIMEOUT_SECONDS",
    "OUTBOX_META_FIELDS",
]
