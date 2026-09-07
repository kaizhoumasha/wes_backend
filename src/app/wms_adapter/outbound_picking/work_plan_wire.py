"""Bin 工作计划的严格请求与封闭响应。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, TypeAdapter, model_validator

from src.app.wms_adapter.outbound_picking.response_wire import (
    ConflictResponse,
    RejectedResponse,
    UnavailableResponse,
)
from src.app.wms_adapter.outbound_picking.wire import BUSINESS_IDENTIFIER_PATTERN
from src.app.wms_adapter.wire_common import NonnegativeMilliseconds, OperationId, StrictWireModel

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


class BinWorkPlanReady(StrictWireModel):
    result: Literal["READY"]
    cell_ids: Annotated[list[Identifier], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_unique_cells(self) -> BinWorkPlanReady:
        if len(set(self.cell_ids)) != len(self.cell_ids):
            raise ValueError("cell_ids 不得重复")
        return self


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


def parse_bin_work_plan_request(value: object) -> BinWorkPlanRequest:
    return BinWorkPlanRequest.model_validate(value)


def parse_bin_work_plan_response(
    status_code: int, value: object, *, request: BinWorkPlanRequest | None = None
) -> BinWorkPlanResponse:
    code = value.get("code") if isinstance(value, dict) else None
    adapter = _RESPONSE_ADAPTERS.get((status_code, code)) if isinstance(code, str) else None
    if adapter is None:
        raise ValueError("HTTP status 与 work_plan response code 不匹配")
    response = adapter.validate_python(value)
    if request is not None and response.operation_id != request.operation_id:
        raise ValueError("响应 operation_id 必须匹配请求")
    return response
