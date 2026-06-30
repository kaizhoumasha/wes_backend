"""WorkLine Service 导出"""

# Phase 2 burn-down 阶段 4 C4a 循环导入防御:
# runtime_reconciliation_service_impl 在模块顶层 import workline.services
# .timeline_sequence_service(走 __getattr__ → 加载 trace.timeline_sequence_service),
# 而 trace.timeline_sequence_service 又依赖 trace.__init__ 提前加载完 trace 子模块。
# C4a 之前,workline.services 顶层 eager 加载 runtime_query_service.py,顺带把
# trace 子模块加载完毕,从而打破循环。C4a 把 runtime_query_service 改为 lazy shim
# 后,这个隐式 priming 失效,导致 runtime → trace → callback → workline.services
# 链上出现部分模块循环。
#
# 防御策略:在 inbox_service / workline_diagnostic_service 等 callback_ingress_service
# 反向依赖 eager 完成之后(否则 callback_ingress_service 反向 import 会得到
# partial module),在 runtime_reconciliation_service 之前,通过
# importlib.import_module 直接加载 query.runtime_query_service 模块,提前完成
# trace 子模块初始化。注意不能直接
# `from ... import runtime_query_service` — 那会再把它绑回 workline.services
# globals,破坏 lazy shim 语义。
import importlib as _importlib

from .device_command_gateway import DeviceCommandGateway, device_command_gateway
from .diagnostic_service import WorklineDiagnosticService, workline_diagnostic_service
from .dispatch_attempt_service import WorklineDispatchAttemptService, workline_dispatch_attempt_service
from .inbox_batch_processor import InboxBatchProcessor
from .inbox_service import WorklineInboxService, inbox_service

# 见文件顶部 C4a 循环导入防御说明。在 inbox_service 等 callback_ingress_service
# 反向依赖 eager 完成之后,提前 prime query.runtime_query_service 与
# intent.smt_inbound_handoff_service,使 trace 子模块提前初始化完成,
# 避免 runtime_reconciliation_service 触发部分模块循环。
_importlib.import_module("src.app.runtime.orchestration.services.query.runtime_query_service")
_importlib.import_module("src.app.runtime.orchestration.services.intent.smt_inbound_handoff_service")

from .object_transition_event_service import ObjectTransitionEventService, object_transition_event_service  # noqa: E402
from .operation_service import WorklineOperationService, workline_operation_service  # noqa: E402
from .outbox_dispatch_service import OutboxDispatchService, outbox_dispatch_service  # noqa: E402
from .rack_position_service import WorklineRackPositionService, workline_rack_position_service  # noqa: E402
from .runtime_reconciliation_service import (  # noqa: E402
    WorklineRuntimeReconciliationService,
    workline_runtime_reconciliation_service,
)
from .safety_service import WorkLineSafetyBlocked, WorkLineSafetyService, workline_safety_service  # noqa: E402
from .workline_service import WorkLineService, workline_service  # noqa: E402
from .write_back_service import OrchestratorWriteBackService, orchestrator_write_back_service  # noqa: E402

__all__ = [
    "BinCellReservationResult",
    "BinCellReservationStatusCode",
    "DeviceCommandGateway",
    "InboxBatchProcessor",
    "NgMaterialConflictError",
    "NgReturnItemService",
    "ObjectTransitionEventService",
    "OrchestratorWriteBackService",
    "OutboxDispatchService",
    "RuntimeHoldCreationService",
    "RuntimeHoldQueryService",
    "RuntimeHoldReleaseService",
    "RuntimeQueryService",
    "SingleLayerRackOrchestrationDecision",
    "SingleLayerRackOrchestrationDecisionCode",
    "SingleLayerRackOrchestrationService",
    "SmtInboundHandoffService",
    "StartAdmissionResult",
    "StartAdmissionStatusFetchResult",
    "StartAdmissionStatusTarget",
    "StationLeaseReasonCode",
    "StationLeaseResult",
    "StationLeaseService",
    "TraceQueryResult",
    "TraceQueryService",
    "WorkLineSafetyBlocked",
    "WorkLineSafetyService",
    "WorkLineService",
    "WorkLineStartAdmissionService",
    "WorklineBinCellReservationService",
    "WorklineDiagnosticService",
    "WorklineDispatchAttemptService",
    "WorklineInboxService",
    "WorklineOperationService",
    "WorklineRackPositionService",
    "WorklineRuntimeReconciliationService",
    "WorklineStationLeaseService",
    "add_timeline_with_sequence",
    "allocate_timeline_seq_no",
    "device_command_gateway",
    "inbox_service",
    "ng_return_item_service",
    "object_transition_event_service",
    "orchestrator_write_back_service",
    "outbox_dispatch_service",
    "runtime_hold_creation_service",
    "runtime_hold_query_service",
    "runtime_hold_release_service",
    "runtime_query_service",
    "single_layer_rack_orchestration_service",
    "smt_inbound_handoff_service",
    "start_admission_service",
    "station_lease_service",
    "trace_query_service",
    "workline_bin_cell_reservation_service",
    "workline_diagnostic_service",
    "workline_dispatch_attempt_service",
    "workline_operation_service",
    "workline_rack_position_service",
    "workline_runtime_reconciliation_service",
    "workline_safety_service",
    "workline_service",
    "workline_station_lease_service",
]


# Phase 2 burn-down 阶段 4:hold/* 与 trace/* service 已迁入
# runtime/orchestration/services/{hold,trace}/。这些 shim 的顶层 import
# 会触发跨子包循环(workline.domain → resource.services →
# workline.services → shim → 回到 hold/trace service)。改用 PEP 562
# module __getattr__ 推迟到首次属性访问时再加载 shim,
# 避免 __init__.py 加载阶段触发循环。
#
# C4a 阶段:intent/smt_inbound_handoff_service 与 query/runtime_query_service
# 也迁入 runtime/orchestration/services/{intent,query}/,继续使用同模式
# 以保持一致并避免后续 capability 重建阶段对 shim import 顺序产生意外依赖。
#
# C4b 阶段:`bin_cell_reservation_service`、`ng_return_item_service`、
# `single_layer_rack_orchestration_service`、`start_admission_service`、
# `station_lease_service` 物理迁入 runtime/capabilities/phase4/。
# 同样 lazy,避免反向回路触发 partial module 循环。
_LAZY_SHIM_MAP = {
    "RuntimeHoldCreationService": "runtime_hold_creation_service",
    "runtime_hold_creation_service": "runtime_hold_creation_service",
    "RuntimeHoldQueryService": "runtime_hold_query_service",
    "runtime_hold_query_service": "runtime_hold_query_service",
    "RuntimeHoldReleaseService": "runtime_hold_release_service",
    "runtime_hold_release_service": "runtime_hold_release_service",
    "RuntimeQueryService": "runtime_query_service",
    "runtime_query_service": "runtime_query_service",
    "SmtInboundHandoffService": "smt_inbound_handoff_service",
    "smt_inbound_handoff_service": "smt_inbound_handoff_service",
    "TraceQueryResult": "trace_query_service",
    "TraceQueryService": "trace_query_service",
    "trace_query_service": "trace_query_service",
    "add_timeline_with_sequence": "timeline_sequence_service",
    "allocate_timeline_seq_no": "timeline_sequence_service",
    # C4b phase4 capabilities 重建 5 service:
    "WorklineBinCellReservationService": "bin_cell_reservation_service",
    "workline_bin_cell_reservation_service": "bin_cell_reservation_service",
    "BinCellReservationResult": "bin_cell_reservation_service",
    "BinCellReservationStatusCode": "bin_cell_reservation_service",
    "NgReturnItemService": "ng_return_item_service",
    "ng_return_item_service": "ng_return_item_service",
    "NgMaterialConflictError": "ng_return_item_service",
    "SingleLayerRackOrchestrationService": "single_layer_rack_orchestration_service",
    "single_layer_rack_orchestration_service": "single_layer_rack_orchestration_service",
    "SingleLayerRackOrchestrationDecision": "single_layer_rack_orchestration_service",
    "SingleLayerRackOrchestrationDecisionCode": "single_layer_rack_orchestration_service",
    "WorkLineStartAdmissionService": "start_admission_service",
    "start_admission_service": "start_admission_service",
    "StartAdmissionResult": "start_admission_service",
    "StartAdmissionStatusFetchResult": "start_admission_service",
    "StartAdmissionStatusTarget": "start_admission_service",
    "StationLeaseService": "station_lease_service",
    "station_lease_service": "station_lease_service",
    "WorklineStationLeaseService": "station_lease_service",
    "workline_station_lease_service": "station_lease_service",
    "StationLeaseResult": "station_lease_service",
    "StationLeaseReasonCode": "station_lease_service",
}


def __getattr__(name: str):
    module_name = _LAZY_SHIM_MAP.get(name)
    if module_name is None:
        raise AttributeError(name)
    import importlib

    module = importlib.import_module(f"src.app.workline.services.{module_name}")
    value = getattr(module, name)
    globals()[name] = value
    return value
