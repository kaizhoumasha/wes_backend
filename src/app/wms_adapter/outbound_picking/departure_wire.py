"""货架离场决定的严格 wire 合同。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, TypeAdapter

from src.app.wms_adapter.outbound_picking.response_wire import ConflictResponse, RejectedResponse, UnavailableResponse
from src.app.wms_adapter.outbound_picking.wire import BUSINESS_IDENTIFIER_PATTERN, RackPosition
from src.app.wms_adapter.wire_common import NonnegativeMilliseconds, OperationId, RackFaceText, StrictWireModel

RACK_DEPARTURE_OPERATION = "outbound.rack.departure_decide@v1"
Identifier = Annotated[str, StringConstraints(pattern=BUSINESS_IDENTIFIER_PATTERN)]


class RackDepartureData(StrictWireModel):
    task_id: Identifier
    rack_id: Identifier
    current_location: RackPosition
    current_face: RackFaceText


class RackDepartureRequest(StrictWireModel):
    operation_id: OperationId
    operation: Literal["outbound.rack.departure_decide@v1"]
    timestamp: NonnegativeMilliseconds
    data: RackDepartureData


class RackDepartureReady(StrictWireModel):
    result: Literal["READY"]
    rack_destination: RackPosition


class RackDepartureWait(StrictWireModel):
    result: Literal["WAIT"]
    retry_after_ms: Annotated[int, Field(ge=1, le=60000)]


class RackDepartureDecidedResponse(StrictWireModel):
    operation_id: OperationId
    code: Literal["DECIDED"]
    timestamp: NonnegativeMilliseconds
    data: Annotated[RackDepartureReady | RackDepartureWait, Field(discriminator="result")]


type RackDepartureResponse = RackDepartureDecidedResponse | UnavailableResponse | ConflictResponse | RejectedResponse

_RESPONSE_ADAPTERS = {
    (200, "DECIDED"): TypeAdapter(RackDepartureDecidedResponse),
    (503, "UNAVAILABLE"): TypeAdapter(UnavailableResponse),
    (409, "CONFLICT"): TypeAdapter(ConflictResponse),
    (422, "REJECTED"): TypeAdapter(RejectedResponse),
}


def parse_rack_departure_request(value: object) -> RackDepartureRequest:
    return RackDepartureRequest.model_validate(value)


def parse_rack_departure_response(
    status_code: int, value: object, *, request: RackDepartureRequest | None = None
) -> RackDepartureResponse:
    code = value.get("code") if isinstance(value, dict) else None
    adapter = _RESPONSE_ADAPTERS.get((status_code, code)) if isinstance(code, str) else None
    if adapter is None:
        raise ValueError("HTTP status 与 departure response code 不匹配")
    response = adapter.validate_python(value)
    if request is not None:
        if response.operation_id != request.operation_id:
            raise ValueError("响应 operation_id 必须匹配请求")
        if (
            isinstance(response, RackDepartureDecidedResponse)
            and isinstance(response.data, RackDepartureReady)
            and response.data.rack_destination == request.data.current_location
        ):
            raise ValueError("离场目标不得等于当前已确认位置")
    return response
