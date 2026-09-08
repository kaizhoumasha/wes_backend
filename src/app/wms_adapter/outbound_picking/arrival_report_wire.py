"""退料货架到位事实的严格请求与封闭响应合同。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import StringConstraints, TypeAdapter

from src.app.wms_adapter.outbound_picking.response_wire import (
    ConflictResponse,
    EmptyResponseData,
    RejectedResponse,
    UnavailableResponse,
)
from src.app.wms_adapter.outbound_picking.wire import BUSINESS_IDENTIFIER_PATTERN, RackPosition
from src.app.wms_adapter.wire_common import (
    NonnegativeMilliseconds,
    OperationId,
    PositiveInteger,
    RackFaceText,
    StrictWireModel,
)
from src.app.wms_diagnostics.observation import WmsCallObservation, observed_contract_error, validate_observed

RETURN_RACK_ARRIVAL_REPORT_OPERATION = "outbound.return_rack.arrival_report@v1"
Identifier = Annotated[str, StringConstraints(pattern=BUSINESS_IDENTIFIER_PATTERN)]
TransportTaskId = Annotated[Identifier, StringConstraints(max_length=80)]


class ReturnRackArrivalReportData(StrictWireModel):
    task_id: Identifier
    transport_task_id: TransportTaskId
    outcome_revision: PositiveInteger
    rack_id: Identifier
    final_position: RackPosition
    arrival_face: RackFaceText


class ReturnRackArrivalReportRequest(StrictWireModel):
    operation_id: OperationId
    operation: Literal["outbound.return_rack.arrival_report@v1"]
    timestamp: NonnegativeMilliseconds
    data: ReturnRackArrivalReportData


class ReturnRackArrivalReportRecordedResponse(StrictWireModel):
    operation_id: OperationId
    code: Literal["RECORDED", "DUPLICATE"]
    timestamp: NonnegativeMilliseconds
    data: EmptyResponseData


type ReturnRackArrivalReportResponse = (
    ReturnRackArrivalReportRecordedResponse | UnavailableResponse | ConflictResponse | RejectedResponse
)

_RESPONSE_ADAPTERS = {
    (200, "RECORDED"): TypeAdapter(ReturnRackArrivalReportRecordedResponse),
    (200, "DUPLICATE"): TypeAdapter(ReturnRackArrivalReportRecordedResponse),
    (503, "UNAVAILABLE"): TypeAdapter(UnavailableResponse),
    (409, "CONFLICT"): TypeAdapter(ConflictResponse),
    (422, "REJECTED"): TypeAdapter(RejectedResponse),
}


def parse_return_rack_arrival_report_request(
    value: object, *, observation: WmsCallObservation | None = None
) -> ReturnRackArrivalReportRequest:
    return validate_observed(ReturnRackArrivalReportRequest, value, observation=observation, side="request")


def parse_return_rack_arrival_report_response(
    status_code: int, value: object, *, observation: WmsCallObservation | None = None
) -> ReturnRackArrivalReportResponse:
    code = value.get("code") if isinstance(value, dict) else None
    adapter = _RESPONSE_ADAPTERS.get((status_code, code)) if isinstance(code, str) else None
    if adapter is None:
        raise observed_contract_error(observation, "HTTP status 与 arrival_report response code 不匹配")
    return validate_observed(adapter, value, observation=observation, side="response")
