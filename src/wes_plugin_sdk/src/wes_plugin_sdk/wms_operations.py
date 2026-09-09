"""插件唯一 WMS facade：固定方法仅构造不可变业务 intent。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from .wms_types import (
    AdmissionIntent,
    BinInboundBatchIntent,
    BinReturnBatchIntent,
    BinReturnCandidate,
    BinWorkPlanIntent,
    CompletionConfirmIntent,
    ManualBinAdmissionIntent,
    ManualBinApplyReportIntent,
    MaterialMovementReportIntent,
    Measurements,
    NgPlacementIntent,
    PickingBinCell,
    PickingMaterialIntent,
    PickingNgZone,
    PickingRackSlot,
    PickingSixInOne,
    PickingTaskPrepareIntent,
    PlacementIntent,
    RackDepartureIntent,
    ReplacementPlanIntent,
    ReturnRackArrivalReportIntent,
    SixInOne,
    SourceEmptyIntent,
    TargetIntent,
)

if TYPE_CHECKING:
    from .decisions import DevicePosition, TransportRackPosition


def inbound_material_admission_decide(
    *,
    material_execution_id: str,
    fact_id: str,
    operation_id: str,
    material_trace_id: str,
    six_in_one: SixInOne,
    measurements: Measurements,
    shape_result: Literal["PASS", "FAIL"],
    workline_code: str,
    source_position: DevicePosition,
) -> AdmissionIntent:
    return AdmissionIntent(
        material_execution_id=material_execution_id,
        fact_id=fact_id,
        operation_id=operation_id,
        material_trace_id=material_trace_id,
        six_in_one=six_in_one,
        measurements=measurements,
        shape_result=shape_result,
        workline_code=workline_code,
        source_position=source_position,
    )


def inbound_material_target_decide(
    *,
    material_execution_id: str,
    fact_id: str,
    operation_id: str,
    material_trace_id: str,
    pkg_id: str,
    inbound_admission_id: str,
    source_position: DevicePosition,
    current_rack_id: str,
) -> TargetIntent:
    return TargetIntent(
        material_execution_id=material_execution_id,
        fact_id=fact_id,
        operation_id=operation_id,
        material_trace_id=material_trace_id,
        pkg_id=pkg_id,
        inbound_admission_id=inbound_admission_id,
        source_position=source_position,
        current_rack_id=current_rack_id,
    )


def inbound_material_placement_report(
    *,
    material_execution_id: str,
    fact_id: str,
    operation_id: str,
    material_trace_id: str,
    pkg_id: str,
    inbound_admission_id: str,
    target_assignment_id: str,
    target_position: DevicePosition,
    placement_sequence: int,
    command_code: str,
    placed_at: int,
) -> PlacementIntent:
    return PlacementIntent(
        material_execution_id=material_execution_id,
        fact_id=fact_id,
        operation_id=operation_id,
        material_trace_id=material_trace_id,
        pkg_id=pkg_id,
        inbound_admission_id=inbound_admission_id,
        target_assignment_id=target_assignment_id,
        target_position=target_position,
        placement_sequence=placement_sequence,
        command_code=command_code,
        placed_at=placed_at,
    )


def inbound_material_ng_placement_report(
    *,
    material_execution_id: str,
    fact_id: str,
    operation_id: str,
    material_trace_id: str,
    ng_evidence_id: str,
    ng_position: DevicePosition,
    reason_code: str,
    business_context: str,
    pkg_id: str | None = None,
) -> NgPlacementIntent:
    return NgPlacementIntent(
        material_execution_id=material_execution_id,
        fact_id=fact_id,
        operation_id=operation_id,
        material_trace_id=material_trace_id,
        ng_evidence_id=ng_evidence_id,
        ng_position=ng_position,
        reason_code=reason_code,
        business_context=business_context,
        pkg_id=pkg_id,
    )


def inbound_source_rack_replacement_plan_decide(
    *,
    material_execution_id: str,
    fact_id: str,
    operation_id: str,
    material_trace_id: str,
    current_rack_id: str,
) -> ReplacementPlanIntent:
    return ReplacementPlanIntent(
        material_execution_id=material_execution_id,
        fact_id=fact_id,
        operation_id=operation_id,
        material_trace_id=material_trace_id,
        current_rack_id=current_rack_id,
    )


def outbound_picking_task_prepare(
    *,
    operation_id: str,
    task_id: str,
    work_line_code: str,
) -> PickingTaskPrepareIntent:
    return PickingTaskPrepareIntent(
        operation_id=operation_id,
        task_id=task_id,
        work_line_code=work_line_code,
    )


def outbound_return_rack_arrival_report(
    *,
    operation_id: str,
    task_id: str,
    transport_task_id: str,
    outcome_revision: int,
    rack_id: str,
    final_position: TransportRackPosition,
    arrival_face: str,
) -> ReturnRackArrivalReportIntent:
    return ReturnRackArrivalReportIntent(
        operation_id=operation_id,
        task_id=task_id,
        transport_task_id=transport_task_id,
        outcome_revision=outcome_revision,
        rack_id=rack_id,
        final_position=final_position,
        arrival_face=arrival_face,
    )


def outbound_bin_inbound_batch(
    *,
    operation_id: str,
    task_id: str,
    rack_id: str,
    rack_face: str,
    max_bin_count: int,
) -> BinInboundBatchIntent:
    return BinInboundBatchIntent(
        operation_id=operation_id,
        task_id=task_id,
        rack_id=rack_id,
        rack_face=rack_face,
        max_bin_count=max_bin_count,
    )


def outbound_bin_work_plan(*, operation_id: str, task_id: str, bin_code: str, scanned_at: int) -> BinWorkPlanIntent:
    return BinWorkPlanIntent(operation_id=operation_id, task_id=task_id, bin_code=bin_code, scanned_at=scanned_at)


def outbound_rack_departure_decide(
    *,
    operation_id: str,
    task_id: str,
    rack_id: str,
    current_location: TransportRackPosition,
    current_face: str,
) -> RackDepartureIntent:
    return RackDepartureIntent(
        operation_id=operation_id,
        task_id=task_id,
        rack_id=rack_id,
        current_location=current_location,
        current_face=current_face,
    )


def outbound_bin_return_batch(
    *,
    operation_id: str,
    workline_code: str,
    rack_id: str,
    rack_face: str,
    return_candidates: tuple[BinReturnCandidate, ...],
) -> BinReturnBatchIntent:
    return BinReturnBatchIntent(
        operation_id=operation_id,
        workline_code=workline_code,
        rack_id=rack_id,
        rack_face=rack_face,
        return_candidates=return_candidates,
    )


def outbound_material_decide(
    *,
    operation_id: str,
    task_id: str,
    source_locator: PickingRackSlot | PickingBinCell,
    six_in_one: PickingSixInOne,
    scanned_at: int,
) -> PickingMaterialIntent:
    return PickingMaterialIntent(
        operation_id=operation_id,
        task_id=task_id,
        source_locator=source_locator,
        six_in_one=six_in_one,
        scanned_at=scanned_at,
    )


def outbound_source_empty_decide(
    *,
    operation_id: str,
    task_id: str,
    source_locator: PickingRackSlot | PickingBinCell,
    observed_at: int,
) -> SourceEmptyIntent:
    return SourceEmptyIntent(
        operation_id=operation_id,
        task_id=task_id,
        source_locator=source_locator,
        observed_at=observed_at,
    )


def outbound_material_movement_report(
    *,
    operation_id: str,
    task_id: str,
    source_locator: PickingRackSlot | PickingBinCell,
    pkg_id: str,
    to_locator: PickingRackSlot | PickingNgZone,
    occurred_at: int,
) -> MaterialMovementReportIntent:
    return MaterialMovementReportIntent(
        operation_id=operation_id,
        task_id=task_id,
        source_locator=source_locator,
        pkg_id=pkg_id,
        to_locator=to_locator,
        occurred_at=occurred_at,
    )


def outbound_picking_task_completion_confirm(
    *,
    operation_id: str,
    task_id: str,
    last_applied_plan_revision: int,
) -> CompletionConfirmIntent:
    return CompletionConfirmIntent(
        operation_id=operation_id,
        task_id=task_id,
        last_applied_plan_revision=last_applied_plan_revision,
    )


def outbound_manual_bin_work_admission(
    *,
    operation_id: str,
    bin_code: str,
    scanned_at: int,
) -> ManualBinAdmissionIntent:
    return ManualBinAdmissionIntent(operation_id=operation_id, bin_code=bin_code, scanned_at=scanned_at)


def outbound_manual_bin_completion_apply_report(
    *,
    operation_id: str,
    completion_operation_id: str,
    task_id: str,
    bin_code: str,
    apply_revision: int,
    apply_result: Literal["APPLIED", "RECONCILING"],
    occurred_at: int,
    reason_code: str | None = None,
) -> ManualBinApplyReportIntent:
    return ManualBinApplyReportIntent(
        operation_id=operation_id,
        completion_operation_id=completion_operation_id,
        task_id=task_id,
        bin_code=bin_code,
        apply_revision=apply_revision,
        apply_result=apply_result,
        occurred_at=occurred_at,
        reason_code=reason_code,
    )
