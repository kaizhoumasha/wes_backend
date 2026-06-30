"""WorkLine Service 导出"""

from .bin_cell_reservation_service import (
    BinCellReservationResult,
    BinCellReservationStatusCode,
    WorklineBinCellReservationService,
    workline_bin_cell_reservation_service,
)
from .debug_data_cleanup_service import DebugDataCleanupService, debug_data_cleanup_service
from .device_command_gateway import DeviceCommandGateway, device_command_gateway
from .diagnostic_service import WorklineDiagnosticService, workline_diagnostic_service
from .dispatch_attempt_service import WorklineDispatchAttemptService, workline_dispatch_attempt_service
from .inbox_batch_processor import InboxBatchProcessor
from .inbox_service import WorklineInboxService, inbox_service

# Phase 2 burn-down 阶段 4:`integration_debug_service` 反向依赖 trace.trace_query_service,
# 顶层 eager import 会在 trace → callback → workline.services 链上构成循环。改为 PEP 562 lazy。
from .ng_return_item_service import NgMaterialConflictError, NgReturnItemService, ng_return_item_service
from .object_transition_event_service import ObjectTransitionEventService, object_transition_event_service
from .operation_service import WorklineOperationService, workline_operation_service
from .outbox_dispatch_service import OutboxDispatchService, outbox_dispatch_service
from .rack_position_service import WorklineRackPositionService, workline_rack_position_service
from .runtime_query_service import RuntimeQueryService, runtime_query_service
from .runtime_reconciliation_service import (
    WorklineRuntimeReconciliationService,
    workline_runtime_reconciliation_service,
)
from .safety_service import WorkLineSafetyBlocked, WorkLineSafetyService, workline_safety_service
from .sandbox_cleanup_service import SandboxCleanupService, sandbox_cleanup_service
from .single_layer_rack_orchestration_service import (
    SingleLayerRackOrchestrationDecision,
    SingleLayerRackOrchestrationDecisionCode,
    SingleLayerRackOrchestrationService,
    single_layer_rack_orchestration_service,
)
from .smt_inbound_handoff_service import SmtInboundHandoffService, smt_inbound_handoff_service
from .start_admission_service import (
    StartAdmissionResult,
    StartAdmissionStatusFetchResult,
    StartAdmissionStatusTarget,
    WorkLineStartAdmissionService,
    start_admission_service,
)
from .station_lease_service import (
    StationLeaseReasonCode,
    StationLeaseResult,
    StationLeaseService,
    WorklineStationLeaseService,
    station_lease_service,
    workline_station_lease_service,
)
from .workline_service import WorkLineService, workline_service
from .write_back_service import OrchestratorWriteBackService, orchestrator_write_back_service

__all__ = [
    "BinCellReservationResult",
    "BinCellReservationStatusCode",
    "DebugDataCleanupService",
    "DeviceCommandGateway",
    "InboxBatchProcessor",
    "IntegrationDebugService",
    "NgMaterialConflictError",
    "NgReturnItemService",
    "ObjectTransitionEventService",
    "OrchestratorWriteBackService",
    "OutboxDispatchService",
    "RuntimeHoldCreationService",
    "RuntimeHoldQueryService",
    "RuntimeHoldReleaseService",
    "RuntimeQueryService",
    "SandboxCleanupService",
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
    "debug_data_cleanup_service",
    "device_command_gateway",
    "inbox_service",
    "integration_debug_service",
    "ng_return_item_service",
    "object_transition_event_service",
    "orchestrator_write_back_service",
    "outbox_dispatch_service",
    "runtime_hold_creation_service",
    "runtime_hold_query_service",
    "runtime_hold_release_service",
    "runtime_query_service",
    "sandbox_cleanup_service",
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
_LAZY_SHIM_MAP = {
    "IntegrationDebugService": "integration_debug_service",
    "integration_debug_service": "integration_debug_service",
    "RuntimeHoldCreationService": "runtime_hold_creation_service",
    "runtime_hold_creation_service": "runtime_hold_creation_service",
    "RuntimeHoldQueryService": "runtime_hold_query_service",
    "runtime_hold_query_service": "runtime_hold_query_service",
    "RuntimeHoldReleaseService": "runtime_hold_release_service",
    "runtime_hold_release_service": "runtime_hold_release_service",
    "TraceQueryResult": "trace_query_service",
    "TraceQueryService": "trace_query_service",
    "trace_query_service": "trace_query_service",
    "add_timeline_with_sequence": "timeline_sequence_service",
    "allocate_timeline_seq_no": "timeline_sequence_service",
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
