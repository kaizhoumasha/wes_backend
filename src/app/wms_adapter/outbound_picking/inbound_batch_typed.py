"""入站批次纯 intent/outcome 与宿主 wire 的静态边界。"""

from typing import Any

import wes_plugin_sdk as sdk

from . import inbound_batch_wire as wire


def encode_request(intent: sdk.BinInboundBatchIntent, *, timestamp: int) -> dict[str, Any]:
    if type(intent) is not sdk.BinInboundBatchIntent:
        raise TypeError("inbound batch requires BinInboundBatchIntent")
    request = wire.parse_bin_inbound_batch_request(
        {
            "operation": wire.BIN_INBOUND_BATCH_OPERATION,
            "operation_id": intent.operation_id,
            "timestamp": timestamp,
            "data": {
                "task_id": intent.task_id,
                "rack_id": intent.rack_id,
                "rack_face": intent.rack_face,
                "max_bin_count": intent.max_bin_count,
            },
        }
    )
    return request.model_dump(mode="json")


def decode_outcome(payload: object) -> sdk.BinInboundBatchOutcome:
    """校验已持久化的响应；HTTP 和请求关联由 Adapter 接收时校验。"""
    code = payload.get("code") if isinstance(payload, dict) else None
    if not isinstance(code, str):
        raise ValueError("inbound batch response code is required")  # noqa: TRY004 - malformed persisted wire.
    status = {"DECIDED": 200, "UNAVAILABLE": 503, "CONFLICT": 409, "REJECTED": 422}.get(code)
    if status is None:
        raise ValueError("unsupported inbound batch response code")
    response = wire.parse_bin_inbound_batch_response(status, payload)
    result: (
        sdk.BinInboundBatchReady
        | sdk.BinBatchNoBatch
        | sdk.BinInboundBatchRackFaceDone
        | sdk.OperationUnavailable
        | sdk.OperationConflict
        | sdk.OperationRejected
    )
    if isinstance(response, wire.BinInboundBatchDecidedResponse):
        data = response.data
        if isinstance(data, wire.BinInboundBatchReady):
            result = sdk.BinInboundBatchReady(
                tuple(
                    sdk.BinInboundBatchMember(
                        member.bin_code,
                        sdk.TransportRackBinSlot(
                            member.source_locator.rack_id,
                            member.source_locator.rack_face,
                            member.source_locator.slot_id,
                        ),
                    )
                    for member in data.bins
                )
            )
        elif isinstance(data, wire.BinBatchNoBatch):
            result = sdk.BinBatchNoBatch(data.retry_after_ms)
        else:
            result = sdk.BinInboundBatchRackFaceDone()
    elif response.code == "UNAVAILABLE":
        result = sdk.OperationUnavailable()
    elif response.code == "CONFLICT":
        result = sdk.OperationConflict(response.data.reason_code)
    else:
        result = sdk.OperationRejected(response.data.reason_code, response.data.field_path)
    return sdk.BinInboundBatchOutcome(result)
