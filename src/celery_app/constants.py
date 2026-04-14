"""
Celery 任务常量定义

定义作业线编排 Celery 任务的运行时常量。
"""

# 外部 HTTP Inbox 类型
EXTERNAL_HTTP_INBOX_KIND = "EXTERNAL_HTTP"

# 外部 HTTP 决策类型
EXTERNAL_HTTP_DECISION_TYPE = "EXTERNAL_HTTP_REQUEST"

# 默认命令参数
DEFAULT_COMMAND_PRIORITY = 5
DEFAULT_COMMAND_TIMEOUT_MS = 300000

# Inbox 处理超时（秒）
# 单条消息处理最大耗时，超过则标记失败并继续处理下一条
INBOX_PROCESS_TIMEOUT_SECONDS = 60

# Outbox 元数据字段
OUTBOX_META_FIELDS = ("command_code", "task_type", "priority", "timeout", "timestamp")

__all__ = [
    "DEFAULT_COMMAND_PRIORITY",
    "DEFAULT_COMMAND_TIMEOUT_MS",
    "EXTERNAL_HTTP_DECISION_TYPE",
    "EXTERNAL_HTTP_INBOX_KIND",
    "INBOX_PROCESS_TIMEOUT_SECONDS",
    "OUTBOX_META_FIELDS",
]
