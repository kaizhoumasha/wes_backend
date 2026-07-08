"""RuntimeInbox 单点消费者入口 (runtime migration 阶段 2, 主计划 §3.5.1)。

阶段 3 时把 inbox 状态机业务逻辑迁入。
"""

from src.app.runtime.orchestration.consumers.runtime_inbox_consumer import (
    RuntimeInboxConsumer,
)
from src.app.runtime.orchestration.consumers.runtime_inbox_service import (
    RuntimeInboxAcceptResult,
    RuntimeInboxConflict,
    RuntimeInboxReplayResult,
    RuntimeInboxService,
    runtime_inbox_service,
)

__all__ = [
    "RuntimeInboxAcceptResult",
    "RuntimeInboxConflict",
    "RuntimeInboxConsumer",
    "RuntimeInboxReplayResult",
    "RuntimeInboxService",
    "runtime_inbox_service",
]
