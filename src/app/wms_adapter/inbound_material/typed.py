"""持久 wire 与 SDK 不可变业务值之间的无 I/O 转换。"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from wes_plugin_sdk import DevicePosition, TransportRackPosition, TransportRackReference, TransportZonePosition
from wes_plugin_sdk import wms_types as sdk

from src.app.wms_adapter.inbound_material import wire


def encode_request(intent: sdk.InboundWmsIntent, *, timestamp: int) -> dict[str, Any]:
    """固定 intent 类型决定 operation；严格 wire parser 继续拥有外部合同。"""
    data = asdict(intent)
    data.pop("fact_id")
    operation_id = data.pop("operation_id")
    if type(intent) is sdk.AdmissionIntent:
        operation = wire.ADMISSION_OPERATION
        data["source_position"] = _handoff(intent.source_position)
    elif type(intent) is sdk.TargetIntent:
        operation = wire.TARGET_OPERATION
        data["source_position"] = _handoff(intent.source_position)
    elif type(intent) is sdk.PlacementIntent:
        operation = wire.PLACEMENT_OPERATION
        data["target_position"] = _cell(intent.target_position)
    elif type(intent) is sdk.NgPlacementIntent:
        operation = wire.NG_PLACEMENT_OPERATION
        data["ng_position"] = {"type": "NG_POSITION", "location_code": intent.ng_position.location_id}
        if intent.pkg_id is None:
            data.pop("pkg_id")
    elif type(intent) is sdk.ReplacementPlanIntent:
        operation = wire.REPLACEMENT_PLAN_OPERATION
    else:
        raise TypeError("unsupported inbound WMS intent")
    return wire.parse_outbound_request(
        {"operation": operation, "operation_id": operation_id, "timestamp": timestamp, "data": data}
    ).model_dump(mode="json", exclude_none=True)


def decode_request(payload: object, *, fact_id: str) -> sdk.InboundWmsIntent:
    request = wire.parse_outbound_request(payload)
    data = request.data.model_dump(mode="json", exclude_none=True)
    data.update(fact_id=fact_id, operation_id=request.operation_id)
    trace = request.data.material_trace_id
    if type(request) is wire.AdmissionRequest:
        data["six_in_one"] = sdk.SixInOne(**data["six_in_one"])
        data["measurements"] = sdk.Measurements(**data["measurements"])
        data["source_position"] = _position(request.data.source_position, trace, "MEASUREMENT_POSITION")
        return sdk.AdmissionIntent(**data)
    if type(request) is wire.TargetRequest:
        data["source_position"] = _position(request.data.source_position, trace, "PIPELINE_OUTLET")
        return sdk.TargetIntent(**data)
    if type(request) is wire.PlacementRequest:
        data["target_position"] = _position(request.data.target_position, trace, "RACK_CELL")
        return sdk.PlacementIntent(**data)
    if type(request) is wire.NgPlacementRequest:
        data["ng_position"] = _position(request.data.ng_position, trace, "NG_POSITION")
        return sdk.NgPlacementIntent(**data)
    if type(request) is wire.ReplacementPlanRequest:
        return sdk.ReplacementPlanIntent(**data)
    raise TypeError("unsupported inbound WMS request")


def decode_outcome(operation: str, payload: object, *, material_trace_id: str) -> sdk.WmsOperationOutcome:
    """读取已持久化响应；业务 Fact 永不携带原始 WMS JSON。"""
    code = payload.get("code") if isinstance(payload, dict) else None
    status = {
        "DECIDED": 200,
        "RECORDED": 200,
        "DUPLICATE": 200,
        "REJECTED": 422,
        "CONFLICT": 409,
        "BUSY": 429,
        "UNAVAILABLE": 503,
    }.get(code)
    if status is None:
        raise ValueError("unsupported WMS response code")
    response = wire.parse_outbound_response(operation, status, payload)
    data = response.data
    if type(response) is wire.RejectedResponse:
        result = sdk.OperationRejected(data.reason_code)
    elif type(response) is wire.ConflictResponse:
        result = sdk.OperationConflict(data.reason_code)
    elif type(response) is wire.BusyResponse:
        result = sdk.OperationBusy(data.retry_after_ms)
    elif type(response) is wire.UnavailableResponse:
        result = sdk.OperationUnavailable()
    elif type(response) is wire.FactResponse:
        result = sdk.FactRecorded(duplicate=response.code == "DUPLICATE")
    elif type(data) is wire.AdmissionAccepted:
        result = sdk.AdmissionAccepted(data.pkg_id, data.inbound_admission_id)
    elif type(data) is wire.Rejected:
        result = sdk.MaterialRejected(
            data.reason_code, _position(data.ng_destination, material_trace_id, "NG_POSITION")
        )
    elif type(data) is wire.Wait:
        result = sdk.OperationWait(data.reason_code, data.retry_after_ms)
    elif type(data) is wire.TargetAssigned:
        result = sdk.TargetAssigned(
            data.target_assignment_id,
            _position(data.target_position, material_trace_id, "RACK_CELL"),
            data.placement_sequence,
            data.expected_height_mm,
        )
    elif type(data) is wire.NoAvailableCell:
        result = sdk.NoAvailableCell(data.reason_code)
    elif type(data) is wire.ReplacementReady:
        result = sdk.ReplacementReady(
            data.rack_replacement_id, _rack_plan(data.old_loaded_rack), _rack_plan(data.new_empty_rack)
        )
    else:
        raise ValueError("unsupported WMS result")
    if operation == wire.ADMISSION_OPERATION:
        return sdk.AdmissionOutcome(result)
    if operation == wire.TARGET_OPERATION:
        return sdk.TargetOutcome(result)
    if operation == wire.PLACEMENT_OPERATION:
        return sdk.PlacementOutcome(result)
    if operation == wire.NG_PLACEMENT_OPERATION:
        return sdk.NgPlacementOutcome(result)
    if operation == wire.REPLACEMENT_PLAN_OPERATION:
        return sdk.ReplacementPlanOutcome(result)
    raise ValueError("unsupported inbound WMS operation")


def _handoff(position: DevicePosition) -> dict[str, str]:
    return {"type": "HANDOFF_POSITION", "location_code": position.location_id}


def _cell(position: DevicePosition) -> dict[str, str | None]:
    return {
        "type": "ONE_LAYER_BIN_CELL",
        "rack_id": position.rack_id,
        "rack_slot_code": position.rack_slot_code,
        "bin_code": position.bin_code,
        "bin_cell_id": position.bin_cell_id,
    }


def _position(
    value: wire.HandoffPosition | wire.NgPosition | wire.OneLayerBinCell, trace: str, kind: str
) -> DevicePosition:
    if type(value) is wire.OneLayerBinCell:
        return DevicePosition(
            value.bin_cell_id, kind, trace, value.rack_id, value.rack_slot_code, value.bin_code, value.bin_cell_id
        )
    return DevicePosition(value.location_code, kind, trace)


def _rack_position(
    value: wire.RackMovePosition,
) -> TransportRackReference | TransportRackPosition | TransportZonePosition:
    if type(value) is wire.RackMoveRackReference:
        return TransportRackReference(value.location_code)
    if type(value) is wire.RackMoveZonePosition:
        return TransportZonePosition(value.location_code)
    return TransportRackPosition(value.location_code)


def _rack_plan(value: wire.RackMovePlan) -> sdk.RackMovePlan:
    return sdk.RackMovePlan(
        value.rack_id, _rack_position(value.source), _rack_position(value.target), value.target_face
    )
