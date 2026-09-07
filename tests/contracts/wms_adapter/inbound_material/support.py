from __future__ import annotations

import hashlib
import json

from src.app.wms_adapter.inbound_material.wire import ADMISSION_OPERATION, NG_PLACEMENT_OPERATION, PLACEMENT_OPERATION
from src.core.outbound_http import OutboundHttpDeliveryState, OutboundHttpResult

OPERATION_ID = "019f12d0-58d7-7b4d-a23a-1b90aa5d4472"
OTHER_OPERATION_ID = "019f12d0-58d7-7b4d-a23a-1b90aa5d4473"
THIRD_OPERATION_ID = "019f12d0-58d7-7b4d-a23a-1b90aa5d4474"


class _Transport:
    def __init__(self, response: OutboundHttpResult) -> None:
        self.response = response
        self.requests = []

    async def send(self, request):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        return self.response

    async def aclose(self) -> None:
        return None


def _request(operation: str = ADMISSION_OPERATION) -> dict[str, object]:
    if operation == NG_PLACEMENT_OPERATION:
        data = {
            "material_execution_id": "EXEC-1",
            "material_trace_id": "TRACE-1",
            "ng_evidence_id": "EVIDENCE-1",
            "ng_position": {"type": "NG_POSITION", "location_code": "NG-1"},
            "reason_code": "BUSINESS_REJECT",
            "business_context": "ROUGH_SORT_INBOUND",
        }
    elif operation == PLACEMENT_OPERATION:
        data = {
            "material_execution_id": "EXEC-1",
            "material_trace_id": "TRACE-1",
            "pkg_id": "PKG-1",
            "inbound_admission_id": "ADM-1",
            "target_assignment_id": "TARGET-1",
            "target_position": {
                "type": "ONE_LAYER_BIN_CELL",
                "rack_id": "RACK-1",
                "rack_slot_code": "SLOT-1",
                "bin_code": "BIN-1",
                "bin_cell_id": "CELL-1",
            },
            "placement_sequence": 1,
            "command_code": "CMD-1",
            "placed_at": 1,
        }
    else:
        data = {
            "material_execution_id": "EXEC-1",
            "material_trace_id": "TRACE-1",
            "six_in_one": {
                "LotCode": "LOT",
                "DateCode": "DATE",
                "Qty": "1",
                "ProductNo": "PN",
                "MfrPN": "MFR",
                "PONumber": "PO",
            },
            "measurements": {"diameter_mm": "1.000", "thickness_mm": "0.500"},
            "shape_result": "PASS",
            "line_run_epoch_id": "EPOCH-1",
            "workline_code": "WL-1",
            "source_position": {"type": "HANDOFF_POSITION", "location_code": "IN-1"},
        }
    return {"operation_id": OPERATION_ID, "operation": operation, "timestamp": 1, "data": data}


def _digest(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _response(body: dict[str, object], *, status: int = 200) -> OutboundHttpResult:
    return OutboundHttpResult(
        delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
        status_code=status,
        response_headers=(("Content-Type", "application/json; charset=utf-8"),),
        decoded_body=json.dumps(body, separators=(",", ":")).encode(),
    )
