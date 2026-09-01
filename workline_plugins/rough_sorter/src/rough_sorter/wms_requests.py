"""粗分业务生成 WMS operation data, 不访问数据库或基础设施。"""

from __future__ import annotations

from typing import Any

from wes_plugin_sdk import DevicePosition

from rough_sorter.facts import (
    DevicePositionConfirmedFact,
    MaterialEvidenceReadyFact,
    TargetDecidedFact,
    TransportOutcomePublishedFact,
)


def wms_position(position: DevicePosition) -> dict[str, str]:
    if position.location_type == "RACK_CELL":
        values = {
            "rack_id": position.rack_id,
            "rack_slot_code": position.rack_slot_code,
            "bin_id": position.bin_id,
            "bin_cell_id": position.bin_cell_id,
        }
        if not all(isinstance(value, str) and value for value in values.values()):
            raise ValueError("RACK_CELL 缺少完整货架/货格身份")
        return {"type": "ONE_LAYER_BIN_CELL", **values}  # type: ignore[dict-item]
    return {
        "type": "NG_POSITION" if position.location_type == "NG_POSITION" else "HANDOFF_POSITION",
        "location_code": position.location_id,
    }


def admission_data(fact: MaterialEvidenceReadyFact) -> dict[str, Any]:
    return {
        "material_execution_id": fact.material_execution_id,
        "material_trace_id": fact.material_trace_id,
        "six_in_one": {
            "LotCode": fact.lot_code,
            "DateCode": fact.date_code,
            "Qty": fact.qty,
            "ProductNo": fact.product_no,
            "MfrPN": fact.mfr_pn,
            "PONumber": fact.po_number,
        },
        "measurements": {"diameter_mm": fact.diameter_mm, "thickness_mm": fact.thickness_mm},
        "shape_result": fact.shape_result.value,
        "line_run_epoch_id": fact.line_run_epoch_id,
        "workline_code": fact.workline_code,
        "source_position": wms_position(fact.source_position),
    }


def target_data(fact: DevicePositionConfirmedFact | TransportOutcomePublishedFact) -> dict[str, Any]:
    source = fact.actual_position if isinstance(fact, DevicePositionConfirmedFact) else fact.source_position
    if source is None:
        raise ValueError("target request 缺少确定 source_position")
    current_rack_id = fact.current_rack_id if isinstance(fact, DevicePositionConfirmedFact) else fact.rack_id
    return {
        "material_execution_id": fact.material_execution_id,
        "material_trace_id": fact.material_trace_id,
        "pkg_id": fact.pkg_id,
        "inbound_admission_id": fact.inbound_admission_id,
        "source_position": wms_position(source),
        "current_rack_id": current_rack_id,
    }


def placement_data(fact: DevicePositionConfirmedFact) -> dict[str, Any]:
    return {
        "material_execution_id": fact.material_execution_id,
        "material_trace_id": fact.material_trace_id,
        "pkg_id": fact.pkg_id,
        "inbound_admission_id": fact.inbound_admission_id,
        "target_assignment_id": fact.target_assignment_id,
        "target_position": wms_position(fact.target_position),
        "placement_sequence": fact.placement_sequence,
        "command_code": fact.command_code,
        "placed_at": fact.placed_at_ms,
    }


def ng_placement_data(fact: DevicePositionConfirmedFact) -> dict[str, Any]:
    return {
        "material_execution_id": fact.material_execution_id,
        "material_trace_id": fact.material_trace_id,
        "ng_evidence_id": fact.ng_evidence_id,
        "ng_position": wms_position(fact.target_position),
        "reason_code": fact.reason_code,
        "business_context": "ROUGH_SORT_INBOUND",
    }


def replacement_plan_data(fact: TargetDecidedFact) -> dict[str, Any]:
    return {
        "material_execution_id": fact.material_execution_id,
        "material_trace_id": fact.material_trace_id,
        "current_rack_id": fact.current_rack_id,
    }


__all__ = [
    "admission_data",
    "ng_placement_data",
    "placement_data",
    "replacement_plan_data",
    "target_data",
    "wms_position",
]
