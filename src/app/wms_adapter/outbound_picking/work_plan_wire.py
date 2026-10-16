"""Bin 工作计划的严格请求与封闭响应。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, FiniteFloat, StrictInt, StringConstraints, TypeAdapter, field_validator

from src.app.wms_adapter.outbound_picking.response_wire import (
    ConflictResponse,
    RejectedResponse,
    UnavailableResponse,
)
from src.app.wms_adapter.outbound_picking.wire import BUSINESS_IDENTIFIER_PATTERN
from src.app.wms_adapter.wire_common import NonnegativeMilliseconds, OperationId, StrictWireModel
from src.app.wms_diagnostics.observation import WmsCallObservation, observed_contract_error, validate_observed

BIN_WORK_PLAN_OPERATION = "outbound.bin.work_plan@v1"
Identifier = Annotated[str, StringConstraints(pattern=BUSINESS_IDENTIFIER_PATTERN)]


class BinWorkPlanData(StrictWireModel):
    task_id: Identifier
    bin_code: Identifier
    scanned_at: NonnegativeMilliseconds


class BinWorkPlanRequest(StrictWireModel):
    operation_id: OperationId
    operation: Literal["outbound.bin.work_plan@v1"]
    timestamp: NonnegativeMilliseconds
    data: BinWorkPlanData


class PickingCellSource(StrictWireModel):
    cell_id: Identifier
    cell_index: Annotated[int, Field(strict=True, ge=1, le=7)]
    bin_type: Literal[2, 3, 6]
    reel_layer: StrictInt
    reel_thickness: Annotated[FiniteFloat, Field(strict=True)]
    reel_diameter: Annotated[FiniteFloat, Field(strict=True)]

    @field_validator("bin_type", mode="before")
    @classmethod
    def validate_bin_type(cls, value: object) -> int:
        if type(value) is not int or value not in (2, 3, 6):
            raise ValueError("bin_type 必须是 2、3 或 6")
        return value


class BinWorkPlanReady(PickingCellSource):
    result: Literal["READY"]


class BinWorkPlanNoWork(StrictWireModel):
    result: Literal["NO_WORK"]


class BinWorkPlanWait(StrictWireModel):
    result: Literal["WAIT"]
    retry_after_ms: Annotated[int, Field(ge=1, le=60000)]


class BinWorkPlanDecidedResponse(StrictWireModel):
    operation_id: OperationId
    code: Literal["DECIDED"]
    timestamp: NonnegativeMilliseconds
    data: Annotated[BinWorkPlanReady | BinWorkPlanNoWork | BinWorkPlanWait, Field(discriminator="result")]


type BinWorkPlanResponse = BinWorkPlanDecidedResponse | UnavailableResponse | ConflictResponse | RejectedResponse

_RESPONSE_ADAPTERS = {
    (200, "DECIDED"): TypeAdapter(BinWorkPlanDecidedResponse),
    (503, "UNAVAILABLE"): TypeAdapter(UnavailableResponse),
    (409, "CONFLICT"): TypeAdapter(ConflictResponse),
    (422, "REJECTED"): TypeAdapter(RejectedResponse),
}


def parse_bin_work_plan_request(value: object, *, observation: WmsCallObservation | None = None) -> BinWorkPlanRequest:
    return validate_observed(BinWorkPlanRequest, value, observation=observation, side="request")


def parse_bin_work_plan_response(
    status_code: int,
    value: object,
    *,
    request: BinWorkPlanRequest | None = None,
    observation: WmsCallObservation | None = None,
) -> BinWorkPlanResponse:
    code = value.get("code") if isinstance(value, dict) else None
    adapter = _RESPONSE_ADAPTERS.get((status_code, code)) if isinstance(code, str) else None
    if adapter is None:
        raise observed_contract_error(observation, "HTTP status 与 work_plan response code 不匹配")
    response = validate_observed(adapter, value, observation=observation, side="response")
    if request is not None and response.operation_id != request.operation_id:
        raise observed_contract_error(
            observation, "响应 operation_id 必须匹配请求", path=("operation_id",), expected_value=request.operation_id
        )
    return response
