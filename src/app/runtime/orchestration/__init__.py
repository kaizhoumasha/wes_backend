"""runtime/orchestration 域核心实体。

主计划 §9.2 7 个 runtime core 实体:
1. ExecutionSession
2. ExecutionCorrelation
3. ExecutionWorkItem
4. RuntimeInbox
5. RuntimeTimeline
6. RuntimeHold
7. RuntimeIntentLog

设计约束 (主计划 §9.2 + §3.5 + target-state-contract.md §4.6):
- execution_session_id 仅在 runtime/orchestration 域内强 FK; 跨域只持
  ExecutionCorrelation.correlation_id (无 session FK 泄漏)
- 对象级 work item 不被 session 串行锁阻塞 (Session 是聚合根, WorkItem 是
  runtime capability 最小推进单位)
- RuntimeInbox status 5 态: RECEIVED -> PROCESSING -> PROCESSED /
  FAILED / DEAD_LETTER
- RuntimeIntentLog 是 outbox/effect ledger, dispatch_status:
  PENDING -> DISPATCHING -> DISPATCHED/ACKED/FAILED
- InboundEventPort / WmsEventPort / DeviceEventPort / RuntimeInbox consumer
  不在业务 capability 注册表 (capability dependency guardrails)

runtime core 实体已落地; ConveyorQueueMembership active/history 投影随 runtime schema 管理。
"""

from src.app.runtime.orchestration.conveyor_queue_membership import ConveyorQueueMembership
from src.app.runtime.orchestration.device_runtime_projection import DeviceRuntimeProjection
from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.execution_work_item import ExecutionWorkItem
from src.app.runtime.orchestration.idempotency_key import IdempotencyKey
from src.app.runtime.orchestration.runtime_hold import RuntimeHold
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
from src.app.runtime.orchestration.runtime_timeline import RuntimeTimeline
from src.app.runtime.orchestration.workline_runtime_status_projection import (
    WorkLineRuntimeStatus,
    WorklineRuntimeStatusProjection,
)

__all__ = [
    "ConveyorQueueMembership",
    "DeviceRuntimeProjection",
    "ExecutionCorrelation",
    "ExecutionSession",
    "ExecutionWorkItem",
    "IdempotencyKey",
    "RuntimeHold",
    "RuntimeInbox",
    "RuntimeIntentLog",
    "RuntimeTimeline",
    "WorkLineRuntimeStatus",
    "WorklineRuntimeStatusProjection",
]
