"""WorkLine Service 导出"""

# Phase 2 burn-down 阶段 6:workline 域退化为纯配置域,运行态 service shim 已
# 物理删除。保留 file 是配置域 service(device_command_gateway / diagnostic_service /
# safety_service / workline_service / write_back_service)。其余 19 个 service
# (dispatch_attempt / inbox / object_transition_event / operation /
# outbox_dispatch / rack_position / runtime_reconciliation / runtime_hold_* /
# runtime_query / smt_inbound_handoff / timeline_sequence / trace_* / phase4
# capabilities)迁入 runtime/orchestration/services 与 runtime/capabilities/phase4/
# 后已物理删除,__getattr__ 命中 `_LAZY_SHIM_MAP` 的 entry 会触发
# ModuleNotFoundError,与原模块行为一致。

from .device_command_gateway import DeviceCommandGateway, device_command_gateway
from .diagnostic_service import WorklineDiagnosticService, workline_diagnostic_service

# 阶段 6:运行态 service shim 已物理删除(`dispatch_attempt_service`、
# `inbox_batch_processor`、`inbox_service`、`object_transition_event_service`、
# `operation_service`、`outbox_dispatch_service`、`rack_position_service`、
# `runtime_reconciliation_service`)。访问这些属性会通过 `_LAZY_SHIM_MAP`
# 命中并由 __getattr__ 触发 ModuleNotFoundError — 与原模块行为一致。
# C4a 循环导入防御:
#   阶段 4 把 `runtime_query_service` / `smt_inbound_handoff_service` 等迁入
#   runtime/orchestration/services/{query,intent}/,workline 顶层保留的 lazy
#   shim 用 importlib.import_module 提前 prime trace 子模块,避免 runtime →
#   trace → callback → workline.services 链上出现部分模块循环。
#   阶段 6 把 workline 端 shim 物理删除后,该 prime 通道同步关闭 —
#   runtime/orchestration 域已独立完成 prime,不再依赖 workline 顶层帮助。
from .safety_service import WorkLineSafetyBlocked, WorkLineSafetyService, workline_safety_service
from .workline_service import WorkLineService, workline_service
from .write_back_service import OrchestratorWriteBackService, orchestrator_write_back_service

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


# Phase 2 burn-down 阶段 6:workline 域退化为纯配置域。所有运行态 service 已
# 迁出(`hold/*`、`trace/*`、`intent/smt_inbound_handoff`、`query/runtime_query`
# 迁入 runtime/orchestration/services/{hold,trace,intent,query}/;5 phase4
# capability 迁入 runtime/capabilities/phase4/),且 workline 顶层 shim 已
# 物理删除。
#
# 本表只保留已删除模块的 lazy entries,attribute access 命中时通过
# importlib.import_module 触发 ModuleNotFoundError — 与 Python 默认
# attribute lookup 抛 AttributeError 不同但对调用方语义一致(都是不可用)。
# 不在表中的属性仍按 PEP 562 默认行为抛 AttributeError。
_LAZY_SHIM_MAP = {
    # 阶段 4 迁出(C4a / C4b):
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
    # 阶段 6 物理删除(运行时态 service 文件已 git rm):
    "WorklineDispatchAttemptService": "dispatch_attempt_service",
    "workline_dispatch_attempt_service": "dispatch_attempt_service",
    "InboxBatchProcessor": "inbox_batch_processor",
    "WorklineInboxService": "inbox_service",
    "inbox_service": "inbox_service",
    "ObjectTransitionEventService": "object_transition_event_service",
    "object_transition_event_service": "object_transition_event_service",
    "WorklineOperationService": "operation_service",
    "workline_operation_service": "operation_service",
    "OutboxDispatchService": "outbox_dispatch_service",
    "outbox_dispatch_service": "outbox_dispatch_service",
    "WorklineRackPositionService": "rack_position_service",
    "workline_rack_position_service": "rack_position_service",
    "WorklineRuntimeReconciliationService": "runtime_reconciliation_service",
    "workline_runtime_reconciliation_service": "runtime_reconciliation_service",
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
