"""单盘放置事实的严格请求与封闭响应合同。"""

from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from src.app.wms_adapter.outbound_picking.material_decide_wire import Identifier, NgZone, PickingBinCell, ScanText
from src.app.wms_adapter.outbound_picking.plan_delta_wire import PlanRackSlot
from src.app.wms_adapter.outbound_picking.response_wire import (
    ConflictResponse,
    EmptyResponseData,
    RejectedResponse,
    UnavailableResponse,
)
from src.app.wms_adapter.wire_common import (
    NonnegativeMilliseconds,
    OperationId,
    StrictWireModel,
)

MATERIAL_MOVEMENT_REPORT_OPERATION = "outbound.material.movement_report@v1"


class MaterialMovementReportData(StrictWireModel):
    task_id: Identifier
    source_locator: Annotated[PlanRackSlot | PickingBinCell, Field(discriminator="type")]
    PkgID: ScanText
    to_locator: Annotated[PlanRackSlot | NgZone, Field(discriminator="type")]
    occurred_at: NonnegativeMilliseconds


class MaterialMovementReportRequest(StrictWireModel):
    operation_id: OperationId
    operation: Literal["outbound.material.movement_report@v1"]
    timestamp: NonnegativeMilliseconds
    data: MaterialMovementReportData


class MaterialMovementReportRecordedResponse(StrictWireModel):
    operation_id: OperationId
    code: Literal["RECORDED", "DUPLICATE"]
    timestamp: NonnegativeMilliseconds
    data: EmptyResponseData


type MaterialMovementReportResponse = (
    MaterialMovementReportRecordedResponse | UnavailableResponse | ConflictResponse | RejectedResponse
)

_RESPONSE_ADAPTERS = {
    (200, "RECORDED"): TypeAdapter(MaterialMovementReportRecordedResponse),
    (200, "DUPLICATE"): TypeAdapter(MaterialMovementReportRecordedResponse),
    (503, "UNAVAILABLE"): TypeAdapter(UnavailableResponse),
    (409, "CONFLICT"): TypeAdapter(ConflictResponse),
    (422, "REJECTED"): TypeAdapter(RejectedResponse),
}


def parse_material_movement_report_request(value: object) -> MaterialMovementReportRequest:
    return MaterialMovementReportRequest.model_validate(value)


def parse_material_movement_report_response(status_code: int, value: object) -> MaterialMovementReportResponse:
    code = value.get("code") if isinstance(value, dict) else None
    adapter = _RESPONSE_ADAPTERS.get((status_code, code)) if isinstance(code, str) else None
    if adapter is None:
        raise ValueError("HTTP status 与 movement_report response code 不匹配")
    return adapter.validate_python(value)
