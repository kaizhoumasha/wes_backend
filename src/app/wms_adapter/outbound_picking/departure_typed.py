"""货架离场纯 SDK 与持久化 wire 边界。"""

from typing import Any

import wes_plugin_sdk as sdk

from . import departure_wire as wire


def encode_request(intent: sdk.RackDepartureIntent, *, timestamp: int) -> dict[str, Any]:
    if type(intent) is not sdk.RackDepartureIntent:
        raise TypeError("departure requires RackDepartureIntent")
    request = wire.parse_rack_departure_request(
        {
            "operation": wire.RACK_DEPARTURE_OPERATION,
            "operation_id": intent.operation_id,
            "timestamp": timestamp,
            "data": {
                "task_id": intent.task_id,
                "rack_id": intent.rack_id,
                "current_location": {"type": "RACK_POSITION", "location_code": intent.current_location.location_code},
                "current_face": intent.current_face,
            },
        }
    )
    return request.model_dump(mode="json")


def decode_outcome(payload: object) -> sdk.RackDepartureOutcome:
    code = payload.get("code") if isinstance(payload, dict) else None
    if not isinstance(code, str):
        raise ValueError("departure response code is required")  # noqa: TRY004 - malformed persisted wire.
    status = {"DECIDED": 200, "UNAVAILABLE": 503, "CONFLICT": 409, "REJECTED": 422}.get(code)
    if status is None:
        raise ValueError("unsupported departure response code")
    response = wire.parse_rack_departure_response(status, payload)
    if isinstance(response, wire.RackDepartureDecidedResponse):
        if isinstance(response.data, wire.RackDepartureReady):
            return sdk.RackDepartureOutcome(
                sdk.RackDepartureReady(sdk.TransportRackPosition(response.data.rack_destination.location_code))
            )
        return sdk.RackDepartureOutcome(sdk.RackDepartureWait(response.data.retry_after_ms))
    if response.code == "UNAVAILABLE":
        return sdk.RackDepartureOutcome(sdk.OperationUnavailable())
    if response.code == "CONFLICT":
        return sdk.RackDepartureOutcome(sdk.OperationConflict(response.data.reason_code))
    return sdk.RackDepartureOutcome(sdk.OperationRejected(response.data.reason_code, response.data.field_path))
