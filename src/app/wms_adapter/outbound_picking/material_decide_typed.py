"""出库物料决定的纯 SDK 与持久化 wire 转换。"""

from dataclasses import asdict
from typing import Any

import wes_plugin_sdk as sdk

from . import material_decide_wire as wire


def encode_request(intent: sdk.PickingMaterialIntent, *, timestamp: int) -> dict[str, Any]:
    if type(intent) is not sdk.PickingMaterialIntent:
        raise TypeError("material decide requires PickingMaterialIntent")
    request = wire.parse_material_decide_request(
        {
            "operation": wire.MATERIAL_DECIDE_OPERATION,
            "operation_id": intent.operation_id,
            "timestamp": timestamp,
            "data": {
                "task_id": intent.task_id,
                "source_locator": {
                    "type": "RACK_SLOT" if type(intent.source_locator) is sdk.PickingRackSlot else "BIN_CELL",
                    **asdict(intent.source_locator),
                },
                "six_in_one": asdict(intent.six_in_one),
                "scanned_at": intent.scanned_at,
            },
        }
    )
    return request.model_dump(mode="json")


def decode_outcome(payload: object) -> sdk.PickingMaterialOutcome:
    code = payload.get("code") if isinstance(payload, dict) else None
    if not isinstance(code, str):
        raise ValueError("material decide response code is required")  # noqa: TRY004 - malformed persisted wire.
    status = {"DECIDED": 200, "UNAVAILABLE": 503, "CONFLICT": 409, "REJECTED": 422}.get(code)
    if status is None:
        raise ValueError("unsupported material decide response code")
    response = wire.parse_material_decide_response(status, payload)
    if isinstance(response, wire.MaterialDecidedResponse):
        data = response.data
        if isinstance(data, wire.MaterialAccept):
            preparation: sdk.PickingTargetRotate | sdk.PickingTargetReplace | None = None
            if isinstance(data.target_preparation, wire.TargetRotate):
                preparation = sdk.PickingTargetRotate()
            elif isinstance(data.target_preparation, wire.TargetReplace):
                preparation = sdk.PickingTargetReplace(
                    sdk.TransportRackPosition(data.target_preparation.rack_destination.location_code)
                )
            target = data.target_locator
            return sdk.PickingMaterialOutcome(
                sdk.PickingMaterialAccept(
                    sdk.PickingRackSlot(target.rack_id, target.rack_face, target.slot_id),
                    data.next_source_action,
                    preparation,
                )
            )
        if isinstance(data, wire.MaterialReject):
            return sdk.PickingMaterialOutcome(
                sdk.PickingMaterialReject(
                    data.business_exception_code, data.ng_locator.zone_code, data.source_disposition
                )
            )
        return sdk.PickingMaterialOutcome(sdk.PickingMaterialWait(data.retry_after_ms))
    if response.code == "UNAVAILABLE":
        return sdk.PickingMaterialOutcome(sdk.OperationUnavailable())
    if response.code == "CONFLICT":
        return sdk.PickingMaterialOutcome(sdk.OperationConflict(response.data.reason_code))
    return sdk.PickingMaterialOutcome(sdk.OperationRejected(response.data.reason_code, response.data.field_path))
