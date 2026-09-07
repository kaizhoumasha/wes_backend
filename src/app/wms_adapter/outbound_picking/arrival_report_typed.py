"""退料货架到位纯 intent/outcome 与宿主 wire 的静态边界。"""

from typing import Any

import wes_plugin_sdk as sdk

from . import arrival_report_wire as wire


def encode_request(intent: sdk.ReturnRackArrivalReportIntent, *, timestamp: int) -> dict[str, Any]:
    if type(intent) is not sdk.ReturnRackArrivalReportIntent:
        raise TypeError("arrival report requires ReturnRackArrivalReportIntent")
    request = wire.parse_return_rack_arrival_report_request(
        {
            "operation": wire.RETURN_RACK_ARRIVAL_REPORT_OPERATION,
            "operation_id": intent.operation_id,
            "timestamp": timestamp,
            "data": {
                "task_id": intent.task_id,
                "transport_task_id": intent.transport_task_id,
                "outcome_revision": intent.outcome_revision,
                "rack_id": intent.rack_id,
                "final_position": {
                    "type": intent.final_position.kind,
                    "location_code": intent.final_position.location_code,
                },
                "arrival_face": intent.arrival_face,
            },
        }
    )
    return request.model_dump(mode="json")


def decode_outcome(payload: object) -> sdk.ReturnRackArrivalReportOutcome:
    """校验已持久化的封闭响应；实际 HTTP status 由 Adapter 接收时校验。"""
    code = payload.get("code") if isinstance(payload, dict) else None
    if not isinstance(code, str):
        raise ValueError("arrival response code is required")  # noqa: TRY004 - malformed persisted wire.
    status = {"RECORDED": 200, "DUPLICATE": 200, "UNAVAILABLE": 503, "CONFLICT": 409, "REJECTED": 422}.get(code)
    if status is None:
        raise ValueError("unsupported arrival response code")
    response = wire.parse_return_rack_arrival_report_response(status, payload)
    result: sdk.FactRecorded | sdk.OperationUnavailable | sdk.OperationConflict | sdk.OperationRejected
    if isinstance(response, wire.ReturnRackArrivalReportRecordedResponse):
        result = sdk.FactRecorded(response.code == "DUPLICATE")
    elif response.code == "UNAVAILABLE":
        result = sdk.OperationUnavailable()
    elif response.code == "CONFLICT":
        result = sdk.OperationConflict(response.data.reason_code)
    else:
        result = sdk.OperationRejected(response.data.reason_code, response.data.field_path)
    return sdk.ReturnRackArrivalReportOutcome(result)
