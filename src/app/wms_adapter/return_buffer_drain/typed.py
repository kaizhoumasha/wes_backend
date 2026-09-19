"""排空 SDK 值与严格 wire 的固定转换。"""

import wes_plugin_sdk as sdk

from . import wire


def encode_request(intent: sdk.ReturnBufferDrainIntent, *, timestamp: int) -> dict:
    if type(intent) is not sdk.ReturnBufferDrainIntent:
        raise TypeError("drain requires ReturnBufferDrainIntent")
    data = {
        "workline_code": intent.workline_code,
        "required_slot_count": intent.required_slot_count,
    }
    return wire.parse_request(
        {
            "operation": wire.RETURN_BUFFER_DRAIN_OPERATION,
            "operation_id": intent.operation_id,
            "timestamp": timestamp,
            "data": data,
        }
    ).model_dump(mode="json", exclude_unset=True)


def decode_outcome(response: wire.DrainResponse) -> sdk.ReturnBufferDrainOutcome:
    """转换已严格校验的响应；同一宿主信任边界内不再次解析原始 JSON。"""
    if response.code == "DECIDED":
        if isinstance(response.data, wire.DrainReady):
            result = sdk.ReturnBufferDrainReady(
                tuple(sdk.RackFaceSequence(rack.rack_id, rack.rack_face) for rack in response.data.racks)
            )
        else:
            result = sdk.ReturnBufferDrainWait(response.data.retry_after_ms, response.data.reason_code)
    elif response.code == "UNAVAILABLE":
        result = sdk.OperationUnavailable()
    elif response.code == "CONFLICT":
        result = sdk.OperationConflict(response.data.reason_code)
    else:
        result = sdk.OperationRejected(response.data.reason_code, response.data.field_path)
    return sdk.ReturnBufferDrainOutcome(result)


def decode_intent(request: wire.DrainRequest) -> sdk.ReturnBufferDrainIntent:
    return sdk.wms_operations.workline_return_buffer_drain_rack_decide(
        operation_id=request.operation_id,
        workline_code=request.data.workline_code,
        required_slot_count=request.data.required_slot_count,
    )
