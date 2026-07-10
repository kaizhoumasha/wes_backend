"""Inbox 子目录 — 入口/批次/分发/转移事件/出口。

从 workline/services/ 物理迁入。
workline/services/ 保留 re-export shim 兼容 v1 API。

TODO(Task 7c): 本目录仍导出 legacy WorklineInboxService (`WorklineInboxService`,
`inbox_service`) 和 `InboxBatchProcessor` (legacy 批处理) 用于:
- callback_ingress_service.process_event / process_external (双写兼容)
- workline.py (legacy celery task, Task 7c 删除)
- inbox_batch_processor (legacy 批处理, Task 7c 删除)
- runtime_reconciliation_service_impl.handle_timer_timeout (处理 WorklineInbox
  TIMER_TIMEOUT, Task 7c 决定是否整体切换到 RuntimeInbox)
- smt_inbound_handoff / runtime_hold_release / runtime_intent_effects
  (Task 7a 已加 TODO 注释, 等 7c 处理)

Task 7b 决策: 保留导出 (brief 禁止删除 legacy 文件), 仅添加此说明
明确哪些调用方仍依赖 legacy export。
"""

from src.app.runtime.orchestration.services.inbox.dispatch_attempt_service import (
    WorklineDispatchAttemptService,
    workline_dispatch_attempt_service,
)
from src.app.runtime.orchestration.services.inbox.inbox_batch_processor import InboxBatchProcessor
from src.app.runtime.orchestration.services.inbox.inbox_service import WorklineInboxService, inbox_service
from src.app.runtime.orchestration.services.inbox.object_transition_event_service import (
    ObjectTransitionEventService,
    object_transition_event_service,
)
from src.app.runtime.orchestration.services.inbox.outbox_dispatch_service import (
    OutboxDispatchService,
    outbox_dispatch_service,
)

__all__ = [
    "InboxBatchProcessor",
    "ObjectTransitionEventService",
    "OutboxDispatchService",
    "WorklineDispatchAttemptService",
    "WorklineInboxService",
    "inbox_service",
    "object_transition_event_service",
    "outbox_dispatch_service",
    "workline_dispatch_attempt_service",
]
