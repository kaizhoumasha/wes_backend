"""Runtime service 按需导出，避免无关调用拉起完整执行闭包。"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_MODULE_EXPORTS = {
    "conveyor_queue_membership_writer_service": (
        "ConveyorQueueMembershipWriteDiagnostics",
        "ConveyorQueueMembershipWriteResult",
        "ConveyorQueueMembershipWriterService",
        "ConveyorQueueWriteBlocked",
        "conveyor_queue_membership_writer_service",
    ),
    "effect_reconciliation_resolution_service": (
        "EffectReconciliationResolutionService",
        "effect_reconciliation_resolution_service",
    ),
    "effect_reducer_service": (
        "EffectIntentNotFound",
        "EffectReducer",
        "EffectReductionResult",
        "InvalidReconciliationEvent",
        "ReconciliationResolutionConflict",
        "effect_reducer",
    ),
    "hold.wms_putaway_sync_barrier_service": (
        "WmsPutawaySyncBarrierEvaluation",
        "WmsPutawaySyncBarrierGroup",
        "WmsPutawaySyncBarrierService",
        "wms_putaway_sync_barrier_service",
    ),
    "idempotency_guard": (
        "ClaimResult",
        "IdempotencyConflict",
        "IdempotencyGuard",
        "idempotency_guard",
        "is_wes_internal_key",
        "make_wes_internal_key",
    ),
    "material_unit_mutation_service": (
        "MaterialUnitMutationService",
        "StaleMaterialUnitPrecondition",
        "material_unit_mutation_service",
    ),
    "rack_demand_service": (
        "RackDemandService",
        "WmsRackDemandClaim",
        "WmsRackDemandReservation",
        "rack_demand_service",
    ),
    "runtime_snapshot_assembler": ("RuntimeSnapshotAssembler", "RuntimeSnapshotInput", "runtime_snapshot_assembler"),
    "session_hold_mutation_service": (
        "SessionHoldMutationService",
        "StaleSessionPrecondition",
        "session_hold_mutation_service",
    ),
    "system_outbox_cancellation_service": (
        "SystemOutboxCancellationService",
        "system_outbox_cancellation_service",
    ),
    "wms_effect_status_service": ("WmsEffectStatusCheckResult", "WmsEffectStatusService", "wms_effect_status_service"),
    "wms_fulfillment_domain_projector": ("WmsFulfillmentDomainProjector", "wms_fulfillment_domain_projector"),
    "workline_runtime_status_projection_service": (
        "WorkLineRuntimeStatusProjectionService",
        "WorkLineRuntimeStatusSnapshot",
        "workline_runtime_status_projection_service",
    ),
}
_EXPORTS = {name: module for module, names in _MODULE_EXPORTS.items() for name in names}
__all__ = sorted(_EXPORTS)  # noqa: PLE0605 - lazy export 表由模块映射生成，不能静态重复维护。


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(name)
    value = getattr(import_module(f"{__name__}.{module_name}"), name)
    globals()[name] = value
    return value
