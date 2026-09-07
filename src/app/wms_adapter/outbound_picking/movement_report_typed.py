"""单盘放置纯 intent/outcome 与宿主 wire 的静态边界。"""

from dataclasses import asdict
from typing import Any

import wes_plugin_sdk as sdk

from . import movement_report_wire as wire


def encode_request(intent: sdk.MaterialMovementReportIntent, *, timestamp: int) -> dict[str, Any]:
    if type(intent) is not sdk.MaterialMovementReportIntent:
        raise TypeError("movement report requires MaterialMovementReportIntent")
    request = wire.parse_material_movement_report_request(
        {
            "operation": wire.MATERIAL_MOVEMENT_REPORT_OPERATION,
            "operation_id": intent.operation_id,
            "timestamp": timestamp,
            "data": {
                "task_id": intent.task_id,
                "source_locator": {
                    "type": "RACK_SLOT" if type(intent.source_locator) is sdk.PickingRackSlot else "BIN_CELL",
                    **asdict(intent.source_locator),
                },
                "PkgID": intent.pkg_id,
                "to_locator": {
                    "type": "RACK_SLOT" if type(intent.to_locator) is sdk.PickingRackSlot else "NG_ZONE",
                    **asdict(intent.to_locator),
                },
                "occurred_at": intent.occurred_at,
            },
        }
    )
    return request.model_dump(mode="json")


def decode_outcome(payload: object) -> sdk.MaterialMovementReportOutcome:
    """校验已持久化的封闭响应；实际 HTTP status 由 Adapter 接收时校验。"""
    code = payload.get("code") if isinstance(payload, dict) else None
    if not isinstance(code, str):
        raise ValueError("movement response code is required")  # noqa: TRY004 - malformed persisted wire.
    status = {"RECORDED": 200, "DUPLICATE": 200, "UNAVAILABLE": 503, "CONFLICT": 409, "REJECTED": 422}.get(code)
    if status is None:
        raise ValueError("unsupported movement response code")
    response = wire.parse_material_movement_report_response(status, payload)
    result: sdk.FactRecorded | sdk.OperationUnavailable | sdk.OperationConflict | sdk.OperationRejected
    if isinstance(response, wire.MaterialMovementReportRecordedResponse):
        result = sdk.FactRecorded(response.code == "DUPLICATE")
    elif response.code == "UNAVAILABLE":
        result = sdk.OperationUnavailable()
    elif response.code == "CONFLICT":
        result = sdk.OperationConflict(response.data.reason_code)
    else:
        result = sdk.OperationRejected(response.data.reason_code, response.data.field_path)
    return sdk.MaterialMovementReportOutcome(result)
