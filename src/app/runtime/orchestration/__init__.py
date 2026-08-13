"""当前总控计划 §9 待清理的 runtime/orchestration 过渡实体。

现存 7 个 runtime core 实体：
1. ExecutionSession
2. ExecutionCorrelation
3. ExecutionWorkItem
4. RuntimeInbox
5. RuntimeTimeline
6. RuntimeHold
7. RuntimeIntentLog

现存过渡模型约束（当前总控计划 §9 清理范围；目标边界见顶层 SPEC §6.1）：
- execution_session_id 仅在 runtime/orchestration 域内强 FK; 跨域只持
  ExecutionCorrelation.correlation_id (无 session FK 泄漏)
- 对象级 work item 不被 session 串行锁阻塞 (Session 是聚合根, WorkItem 是
  runtime capability 最小推进单位)
- RuntimeInbox status 5 态: RECEIVED -> PROCESSING -> PROCESSED /
  FAILED / DEAD_LETTER
- RuntimeIntentLog 是 capability EFFECT 语义账本，transport 状态只归 SystemOutbox。
- ReconciliationCase 是 EFFECT UNKNOWN/矛盾 evidence 的独立 OPEN/RESOLVED 裁决对象。
- InboundEventPort / WmsEventPort / DeviceEventPort / RuntimeInbox consumer
  不在业务 capability 注册表 (capability dependency guardrails)

runtime core 实体已落地; ConveyorQueueMembership active/history 投影随 runtime schema 管理。
"""

from src.app.runtime.orchestration.bin_route_instance import BinRouteInstance
from src.app.runtime.orchestration.conveyor_queue_membership import ConveyorQueueMembership
from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.execution_work_item import ExecutionWorkItem
from src.app.runtime.orchestration.idempotency_key import IdempotencyKey
from src.app.runtime.orchestration.material_flow_owner import MaterialFlowOwner
from src.app.runtime.orchestration.reconciliation_case import ReconciliationCase, ReconciliationCaseStatus
from src.app.runtime.orchestration.runtime_hold import RuntimeHold
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog, RuntimeIntentStatus
from src.app.runtime.orchestration.runtime_timeline import RuntimeTimeline
from src.app.runtime.orchestration.wms_rack_demand import WmsRackDemand
from src.app.runtime.orchestration.workline_runtime_status_projection import (
    WorkLineRuntimeStatus,
    WorklineRuntimeStatusProjection,
)

__all__ = [
    "BinRouteInstance",
    "ConveyorQueueMembership",
    "ExecutionCorrelation",
    "ExecutionSession",
    "ExecutionWorkItem",
    "IdempotencyKey",
    "MaterialFlowOwner",
    "ReconciliationCase",
    "ReconciliationCaseStatus",
    "RuntimeHold",
    "RuntimeInbox",
    "RuntimeIntentLog",
    "RuntimeIntentStatus",
    "RuntimeTimeline",
    "WmsRackDemand",
    "WorkLineRuntimeStatus",
    "WorklineRuntimeStatusProjection",
]
