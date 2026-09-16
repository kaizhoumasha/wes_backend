"""排空 SDK 值与严格 wire 的固定转换。"""

import wes_plugin_sdk as sdk

from . import wire


def encode_request(intent: sdk.ReturnBufferDrainIntent, *, timestamp: int) -> dict:
    if type(intent) is not sdk.ReturnBufferDrainIntent:
        raise TypeError("drain requires ReturnBufferDrainIntent")
    data = {
        "workline_code": intent.workline_code,
        "plugin_key": intent.plugin_key,
        "drain_reason": intent.drain_reason,
        "return_candidates": [
            {
                "sequence_no": c.sequence_no,
                "bin_code": c.bin_code,
                "source": {"type": "HANDOFF_POSITION", "location_code": c.source_location_code},
            }
            for c in intent.return_candidates
        ],
    }
    if intent.previous_operation_id is not None:
        data["previous_operation_id"] = intent.previous_operation_id
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
            result = sdk.ReturnBufferDrainReady(response.data.rack_id, response.data.rack_face)
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
        plugin_key=request.data.plugin_key,
        drain_reason=request.data.drain_reason,
        previous_operation_id=request.data.previous_operation_id,
        return_candidates=tuple(
            sdk.BinReturnCandidate(c.sequence_no, c.bin_code, c.source.location_code)
            for c in request.data.return_candidates
        ),
    )
