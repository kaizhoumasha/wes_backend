"""确定空取的严格请求与封闭决定。"""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, TypeAdapter

from src.app.wms_adapter.outbound_picking.material_decide_wire import PickingBinCell
from src.app.wms_adapter.outbound_picking.plan_delta_wire import PlanRackSlot
from src.app.wms_adapter.outbound_picking.response_wire import ConflictResponse, RejectedResponse, UnavailableResponse
from src.app.wms_adapter.outbound_picking.wire import BUSINESS_IDENTIFIER_PATTERN
from src.app.wms_adapter.wire_common import NonnegativeMilliseconds, OperationId, StrictWireModel

SOURCE_EMPTY_OPERATION = "outbound.source.empty_decide@v1"


class SourceEmptyData(StrictWireModel):
    task_id: Annotated[str, StringConstraints(pattern=BUSINESS_IDENTIFIER_PATTERN)]
    source_locator: Annotated[PlanRackSlot | PickingBinCell, Field(discriminator="type")]
    observed_at: NonnegativeMilliseconds


class SourceEmptyRequest(StrictWireModel):
    operation_id: OperationId
    operation: Literal["outbound.source.empty_decide@v1"]
    timestamp: NonnegativeMilliseconds
    data: SourceEmptyData


class SourceEmptyRetry(StrictWireModel):
    result: Literal["RETRY"]


class SourceEmptyDone(StrictWireModel):
    result: Literal["SOURCE_DONE"]


class SourceEmptyWait(StrictWireModel):
    result: Literal["WAIT"]
    retry_after_ms: Annotated[int, Field(ge=1, le=60000)]


class SourceEmptyDecidedResponse(StrictWireModel):
    operation_id: OperationId
    code: Literal["DECIDED"]
    timestamp: NonnegativeMilliseconds
    data: Annotated[SourceEmptyRetry | SourceEmptyDone | SourceEmptyWait, Field(discriminator="result")]


type SourceEmptyResponse = SourceEmptyDecidedResponse | UnavailableResponse | ConflictResponse | RejectedResponse

_RESPONSE_ADAPTERS = {
    (200, "DECIDED"): TypeAdapter(SourceEmptyDecidedResponse),
    (503, "UNAVAILABLE"): TypeAdapter(UnavailableResponse),
    (409, "CONFLICT"): TypeAdapter(ConflictResponse),
    (422, "REJECTED"): TypeAdapter(RejectedResponse),
}


def parse_source_empty_request(value: object) -> SourceEmptyRequest:
    return SourceEmptyRequest.model_validate(value)


def parse_source_empty_response(
    status_code: int, value: object, *, request: SourceEmptyRequest | None = None
) -> SourceEmptyResponse:
    code = value.get("code") if isinstance(value, dict) else None
    adapter = _RESPONSE_ADAPTERS.get((status_code, code)) if isinstance(code, str) else None
    if adapter is None:
        raise ValueError("HTTP status 与 source empty response code 不匹配")
    response = adapter.validate_python(value)
    if request is not None and response.operation_id != request.operation_id:
        raise ValueError("响应 operation_id 必须匹配请求")
    return response
