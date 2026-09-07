"""退箱 SDK 纯值与严格 wire 的固定转换。"""

import wes_plugin_sdk as sdk

from . import return_batch_wire as wire


def encode_request(intent: sdk.BinReturnBatchIntent, *, timestamp: int) -> dict:
    if type(intent) is not sdk.BinReturnBatchIntent:
        raise TypeError("return batch requires BinReturnBatchIntent")
    return wire.parse_bin_return_batch_request(
        {
            "operation": wire.BIN_RETURN_BATCH_OPERATION,
            "operation_id": intent.operation_id,
            "timestamp": timestamp,
            "data": {
                "workline_code": intent.workline_code,
                "rack_id": intent.rack_id,
                "rack_face": intent.rack_face,
                "return_candidates": [
                    {
                        "sequence_no": c.sequence_no,
                        "bin_code": c.bin_code,
                        "source": {"type": "HANDOFF_POSITION", "location_code": c.source_location_code},
                    }
                    for c in intent.return_candidates
                ],
            },
        }
    ).model_dump(mode="json")


def decode_outcome(payload: object) -> sdk.BinReturnBatchOutcome:
    code = payload.get("code") if isinstance(payload, dict) else None
    status = (
        {"DECIDED": 200, "UNAVAILABLE": 503, "CONFLICT": 409, "REJECTED": 422}.get(code)
        if isinstance(code, str)
        else None
    )
    if status is None:
        raise ValueError("unsupported return batch response")
    response = wire.parse_bin_return_batch_response(status, payload)
    if response.code == "DECIDED":
        if isinstance(response.data, wire.BinReturnBatchReady):
            result = sdk.BinReturnBatchReady(
                tuple(
                    sdk.BinReturnMove(
                        m.sequence_no,
                        m.bin_code,
                        sdk.TransportRackBinSlot(m.target.rack_id, m.target.rack_face, m.target.slot_id),
                    )
                    for m in response.data.moves
                )
            )
        else:
            result = sdk.BinBatchNoBatch(response.data.retry_after_ms)
    elif response.code == "UNAVAILABLE":
        result = sdk.OperationUnavailable()
    elif response.code == "CONFLICT":
        result = sdk.OperationConflict(response.data.reason_code)
    else:
        result = sdk.OperationRejected(response.data.reason_code, response.data.field_path)
    return sdk.BinReturnBatchOutcome(result)
