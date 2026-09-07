"""空取决定的纯 SDK 与持久化 wire 转换。"""

from dataclasses import asdict
from typing import Any

import wes_plugin_sdk as sdk

from . import source_empty_wire as wire


def encode_request(intent: sdk.SourceEmptyIntent, *, timestamp: int) -> dict[str, Any]:
    if type(intent) is not sdk.SourceEmptyIntent:
        raise TypeError("source empty requires SourceEmptyIntent")
    request = wire.parse_source_empty_request(
        {
            "operation": wire.SOURCE_EMPTY_OPERATION,
            "operation_id": intent.operation_id,
            "timestamp": timestamp,
            "data": {
                "task_id": intent.task_id,
                "source_locator": {
                    "type": "RACK_SLOT" if type(intent.source_locator) is sdk.PickingRackSlot else "BIN_CELL",
                    **asdict(intent.source_locator),
                },
                "observed_at": intent.observed_at,
            },
        }
    )
    return request.model_dump(mode="json")


def decode_outcome(payload: object) -> sdk.SourceEmptyOutcome:
    code = payload.get("code") if isinstance(payload, dict) else None
    if not isinstance(code, str):
        raise ValueError("source empty response code is required")  # noqa: TRY004 - malformed persisted wire.
    status = {"DECIDED": 200, "UNAVAILABLE": 503, "CONFLICT": 409, "REJECTED": 422}.get(code)
    if status is None:
        raise ValueError("unsupported source empty response code")
    response = wire.parse_source_empty_response(status, payload)
    if isinstance(response, wire.SourceEmptyDecidedResponse):
        if isinstance(response.data, wire.SourceEmptyRetry):
            return sdk.SourceEmptyOutcome(sdk.SourceEmptyRetry())
        if isinstance(response.data, wire.SourceEmptyDone):
            return sdk.SourceEmptyOutcome(sdk.SourceEmptyDone())
        return sdk.SourceEmptyOutcome(sdk.SourceEmptyWait(response.data.retry_after_ms))
    if response.code == "UNAVAILABLE":
        return sdk.SourceEmptyOutcome(sdk.OperationUnavailable())
    if response.code == "CONFLICT":
        return sdk.SourceEmptyOutcome(sdk.OperationConflict(response.data.reason_code))
    return sdk.SourceEmptyOutcome(sdk.OperationRejected(response.data.reason_code, response.data.field_path))
