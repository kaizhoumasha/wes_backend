"""出库物料决定的严格 wire 合同；业务准入与物理执行归插件。"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, TypeAdapter, field_validator, model_validator

from src.app.wms_adapter.outbound_picking.plan_delta_wire import PlanRackFace, PlanRackSlot
from src.app.wms_adapter.outbound_picking.response_wire import ConflictResponse, RejectedResponse, UnavailableResponse
from src.app.wms_adapter.outbound_picking.wire import BUSINESS_IDENTIFIER_PATTERN, RackPosition
from src.app.wms_adapter.wire_common import NonnegativeMilliseconds, OperationId, StrictWireModel

MATERIAL_DECIDE_OPERATION = "outbound.material.decide@v1"
Identifier = Annotated[str, StringConstraints(pattern=BUSINESS_IDENTIFIER_PATTERN)]
ScanText = Annotated[str, StringConstraints(min_length=1, max_length=256)]


class PickingBinCell(PlanRackFace):
    type: Literal["BIN_CELL"]
    bin_code: Identifier
    cell_id: Identifier


class PickingSixInOne(StrictWireModel):
    HHPN: ScanText
    MfrPN: ScanText
    Qty: ScanText
    DateCode: ScanText
    LotCode: ScanText
    PkgID: ScanText


class MaterialDecideData(StrictWireModel):
    task_id: Identifier
    source_locator: Annotated[PlanRackSlot | PickingBinCell, Field(discriminator="type")]
    six_in_one: PickingSixInOne
    scanned_at: NonnegativeMilliseconds


class MaterialDecideRequest(StrictWireModel):
    operation_id: OperationId
    operation: Literal["outbound.material.decide@v1"]
    timestamp: NonnegativeMilliseconds
    data: MaterialDecideData


class TargetRotate(StrictWireModel):
    mode: Literal["ROTATE"]


class TargetReplace(StrictWireModel):
    mode: Literal["REPLACE"]
    rack_destination: RackPosition


class MaterialAccept(StrictWireModel):
    result: Literal["ACCEPT"]
    target_locator: PlanRackSlot
    next_source_action: Literal["CONTINUE", "SOURCE_DONE"]
    target_preparation: Annotated[TargetRotate | TargetReplace, Field(discriminator="mode")] | None = None

    @field_validator("target_preparation", mode="before")
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("target_preparation 无动作时必须省略")
        return value


class NgZone(StrictWireModel):
    type: Literal["NG_ZONE"]
    zone_code: Identifier


class MaterialReject(StrictWireModel):
    result: Literal["REJECT"]
    business_exception_code: Literal["MATERIAL_REJECTED", "SOURCE_CELL_MISMATCH"]
    ng_locator: NgZone
    source_disposition: Literal["CONTINUE", "CLOSE"]

    @model_validator(mode="after")
    def validate_cell_closure(self) -> Self:
        if self.business_exception_code == "SOURCE_CELL_MISMATCH" and self.source_disposition != "CLOSE":
            raise ValueError("SOURCE_CELL_MISMATCH 必须 CLOSE")
        return self


class MaterialWait(StrictWireModel):
    result: Literal["WAIT"]
    retry_after_ms: Annotated[int, Field(ge=1, le=60000)]


class MaterialDecidedResponse(StrictWireModel):
    operation_id: OperationId
    code: Literal["DECIDED"]
    timestamp: NonnegativeMilliseconds
    data: Annotated[MaterialAccept | MaterialReject | MaterialWait, Field(discriminator="result")]


type MaterialDecideResponse = MaterialDecidedResponse | UnavailableResponse | ConflictResponse | RejectedResponse

_RESPONSE_ADAPTERS = {
    (200, "DECIDED"): TypeAdapter(MaterialDecidedResponse),
    (503, "UNAVAILABLE"): TypeAdapter(UnavailableResponse),
    (409, "CONFLICT"): TypeAdapter(ConflictResponse),
    (422, "REJECTED"): TypeAdapter(RejectedResponse),
}


def parse_material_decide_request(value: object) -> MaterialDecideRequest:
    return MaterialDecideRequest.model_validate(value)


def parse_material_decide_response(
    status_code: int, value: object, *, request: MaterialDecideRequest | None = None
) -> MaterialDecideResponse:
    code = value.get("code") if isinstance(value, dict) else None
    adapter = _RESPONSE_ADAPTERS.get((status_code, code)) if isinstance(code, str) else None
    if adapter is None:
        raise ValueError("HTTP status 与 material decide response code 不匹配")
    response = adapter.validate_python(value)
    if request is not None:
        if response.operation_id != request.operation_id:
            raise ValueError("响应 operation_id 必须匹配请求")
        if isinstance(response, MaterialDecidedResponse) and isinstance(request.data.source_locator, PlanRackSlot):
            data = response.data
            if isinstance(data, MaterialAccept) and data.next_source_action != "SOURCE_DONE":
                raise ValueError("RACK_SLOT 来源必须 SOURCE_DONE")
            if isinstance(data, MaterialReject) and (
                data.business_exception_code != "MATERIAL_REJECTED" or data.source_disposition != "CLOSE"
            ):
                raise ValueError("RACK_SLOT 来源只允许 MATERIAL_REJECTED / CLOSE")
    return response
