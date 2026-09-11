"""人工工作位 Bin 任务准入的严格 wire 合同。"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, TypeAdapter, model_validator

from src.app.wms_adapter.outbound_picking.response_wire import ConflictResponse, RejectedResponse, UnavailableResponse
from src.app.wms_adapter.outbound_picking.wire import BUSINESS_IDENTIFIER_PATTERN
from src.app.wms_adapter.wire_common import OperationId, PositiveMilliseconds, StrictWireModel
from src.app.wms_diagnostics.observation import WmsCallObservation, observed_contract_error, validate_observed

MANUAL_BIN_ADMISSION_OPERATION = "outbound.manual_bin.work_admission_decide@v1"
Identifier = Annotated[str, StringConstraints(pattern=BUSINESS_IDENTIFIER_PATTERN)]


class ManualBinAdmissionData(StrictWireModel):
    task_id: Identifier
    bin_code: Identifier
    scanned_at: PositiveMilliseconds


class ManualBinAdmissionRequest(StrictWireModel):
    operation_id: OperationId
    operation: Literal["outbound.manual_bin.work_admission_decide@v1"]
    timestamp: PositiveMilliseconds
    data: ManualBinAdmissionData

    @model_validator(mode="after")
    def validate_scan_time(self) -> Self:
        if self.data.scanned_at > self.timestamp:
            raise ValueError("scanned_at 不得晚于 timestamp")
        return self


class ManualBinWorkRequired(StrictWireModel):
    result: Literal["WORK_REQUIRED"]
    task_id: Identifier


class ManualBinNoWork(StrictWireModel):
    result: Literal["NO_WORK"]


class ManualBinWait(StrictWireModel):
    result: Literal["WAIT"]
    retry_after_ms: Annotated[int, Field(ge=1, le=60000)]


class ManualBinAdmissionDecidedResponse(StrictWireModel):
    operation_id: OperationId
    code: Literal["DECIDED"]
    timestamp: PositiveMilliseconds
    data: Annotated[ManualBinWorkRequired | ManualBinNoWork | ManualBinWait, Field(discriminator="result")]


type ManualBinAdmissionResponse = (
    ManualBinAdmissionDecidedResponse | UnavailableResponse | ConflictResponse | RejectedResponse
)

_RESPONSE_ADAPTERS = {
    (200, "DECIDED"): TypeAdapter(ManualBinAdmissionDecidedResponse),
    (503, "UNAVAILABLE"): TypeAdapter(UnavailableResponse),
    (409, "CONFLICT"): TypeAdapter(ConflictResponse),
    (422, "REJECTED"): TypeAdapter(RejectedResponse),
}


def parse_manual_bin_admission_request(
    value: object, *, observation: WmsCallObservation | None = None
) -> ManualBinAdmissionRequest:
    return validate_observed(ManualBinAdmissionRequest, value, observation=observation, side="request")


def parse_manual_bin_admission_response(
    status_code: int,
    value: object,
    *,
    request: ManualBinAdmissionRequest | None = None,
    observation: WmsCallObservation | None = None,
) -> ManualBinAdmissionResponse:
    code = value.get("code") if isinstance(value, dict) else None
    adapter = _RESPONSE_ADAPTERS.get((status_code, code)) if isinstance(code, str) else None
    if adapter is None:
        raise observed_contract_error(observation, "HTTP status 与 manual bin admission response code 不匹配")
    response = validate_observed(adapter, value, observation=observation, side="response")
    if request is not None and response.operation_id != request.operation_id:
        raise observed_contract_error(observation, "响应 operation_id 必须匹配请求", path=("operation_id",))
    if (
        request is not None
        and isinstance(response, ManualBinAdmissionDecidedResponse)
        and isinstance(response.data, ManualBinWorkRequired)
        and response.data.task_id != request.data.task_id
    ):
        raise observed_contract_error(observation, "响应 task_id 必须匹配请求", path=("data", "task_id"))
    return response


__all__ = [
    "MANUAL_BIN_ADMISSION_OPERATION",
    "ManualBinAdmissionDecidedResponse",
    "ManualBinAdmissionRequest",
    "parse_manual_bin_admission_request",
    "parse_manual_bin_admission_response",
]
