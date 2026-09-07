"""粗分业务通过固定 typed methods 创建不可变 WMS intent。"""

from __future__ import annotations

from typing import cast

from wes_plugin_sdk import (
    AdmissionIntent,
    Measurements,
    NgPlacementIntent,
    PlacementIntent,
    ReplacementPlanIntent,
    SixInOne,
    TargetIntent,
    wms_operations,
)

from rough_sorter.facts import (
    DevicePositionConfirmedFact,
    MaterialEvidenceReadyFact,
    TargetDecidedFact,
    TransportOutcomePublishedFact,
)


def admission_data(fact: MaterialEvidenceReadyFact) -> AdmissionIntent:
    return wms_operations.inbound_material_admission_decide(
        material_execution_id=fact.material_execution_id,
        fact_id=fact.fact_id,
        operation_id=fact.request_operation_id,
        material_trace_id=fact.material_trace_id,
        six_in_one=SixInOne(
            LotCode=fact.lot_code,
            DateCode=fact.date_code,
            Qty=fact.qty,
            ProductNo=fact.product_no,
            MfrPN=fact.mfr_pn,
            PONumber=fact.po_number,
        ),
        measurements=Measurements(diameter_mm=fact.diameter_mm, thickness_mm=fact.thickness_mm),
        shape_result=fact.shape_result.value,
        line_run_epoch_id=fact.line_run_epoch_id,
        workline_code=fact.workline_code,
        source_position=fact.source_position,
    )


def target_data(fact: DevicePositionConfirmedFact | TransportOutcomePublishedFact) -> TargetIntent:
    source = fact.actual_position if isinstance(fact, DevicePositionConfirmedFact) else fact.source_position
    if source is None:
        raise ValueError("target request 缺少确定 source_position")
    return wms_operations.inbound_material_target_decide(
        material_execution_id=fact.material_execution_id,
        fact_id=fact.fact_id,
        operation_id=fact.request_operation_id or "",
        material_trace_id=fact.material_trace_id,
        pkg_id=cast("str", fact.pkg_id),
        inbound_admission_id=cast("str", fact.inbound_admission_id),
        source_position=source,
        current_rack_id=cast("str", fact.current_rack_id)
        if isinstance(fact, DevicePositionConfirmedFact)
        else fact.rack_id,
    )


def placement_data(fact: DevicePositionConfirmedFact) -> PlacementIntent:
    return wms_operations.inbound_material_placement_report(
        material_execution_id=fact.material_execution_id,
        fact_id=fact.fact_id,
        operation_id=fact.request_operation_id or "",
        material_trace_id=fact.material_trace_id,
        pkg_id=cast("str", fact.pkg_id),
        inbound_admission_id=cast("str", fact.inbound_admission_id),
        target_assignment_id=cast("str", fact.target_assignment_id),
        target_position=fact.target_position,
        placement_sequence=cast("int", fact.placement_sequence),
        command_code=fact.command_code,
        placed_at=cast("int", fact.placed_at_ms),
    )


def ng_placement_data(fact: DevicePositionConfirmedFact) -> NgPlacementIntent:
    return wms_operations.inbound_material_ng_placement_report(
        material_execution_id=fact.material_execution_id,
        fact_id=fact.fact_id,
        operation_id=fact.request_operation_id or "",
        material_trace_id=fact.material_trace_id,
        ng_evidence_id=cast("str", fact.ng_evidence_id),
        ng_position=fact.target_position,
        reason_code=cast("str", fact.reason_code),
        business_context="ROUGH_SORT_INBOUND",
    )


def replacement_plan_data(fact: TargetDecidedFact) -> ReplacementPlanIntent:
    return wms_operations.inbound_source_rack_replacement_plan_decide(
        material_execution_id=fact.material_execution_id,
        fact_id=fact.fact_id,
        operation_id=fact.request_operation_id or "",
        material_trace_id=fact.material_trace_id,
        current_rack_id=fact.current_rack_id,
    )
