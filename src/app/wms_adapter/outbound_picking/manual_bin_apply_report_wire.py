"""人工工作位完成决定应用结果的严格 wire 合同。"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, TypeAdapter, model_validator

from src.app.wms_adapter.outbound_picking.response_wire import (
    ConflictResponse,
    EmptyResponseData,
    RejectedResponse,
    UnavailableResponse,
)
from src.app.wms_adapter.outbound_picking.wire import BUSINESS_IDENTIFIER_PATTERN
from src.app.wms_adapter.wire_common import OperationId, PositiveMilliseconds, StrictWireModel
from src.app.wms_diagnostics.observation import WmsCallObservation, observed_contract_error, validate_observed

MANUAL_BIN_APPLY_REPORT_OPERATION = "outbound.manual_bin.completion_apply_report@v1"
Identifier = Annotated[str, StringConstraints(pattern=BUSINESS_IDENTIFIER_PATTERN)]


class ManualBinApplied(StrictWireModel):
    completion_operation_id: OperationId
    task_id: Identifier
    bin_code: Identifier
    apply_revision: Annotated[int, Field(ge=1)]
    apply_result: Literal["APPLIED"]
    occurred_at: PositiveMilliseconds


class ManualBinReconciling(StrictWireModel):
    completion_operation_id: OperationId
    task_id: Identifier
    bin_code: Identifier
    apply_revision: Annotated[int, Field(ge=1)]
    apply_result: Literal["RECONCILING"]
    reason_code: Literal[
        "RESULT_CONFLICT",
        "FIRST_COMPLETION_OUT_OF_WINDOW",
        "POINT2_BINDING_MISMATCH",
        "WORKLINE_NOT_ACTIVE",
        "COMPLETED_AT_INVALID",
        "DEVICE_COMMAND_IDENTITY_CONFLICT",
    ]
    occurred_at: PositiveMilliseconds


class ManualBinApplyReportRequest(StrictWireModel):
    operation_id: OperationId
    operation: Literal["outbound.manual_bin.completion_apply_report@v1"]
    timestamp: PositiveMilliseconds
    data: Annotated[ManualBinApplied | ManualBinReconciling, Field(discriminator="apply_result")]

    @model_validator(mode="after")
    def validate_occurred_time(self) -> Self:
        if self.data.occurred_at > self.timestamp:
            raise ValueError("occurred_at 不得晚于 timestamp")
        return self


class ManualBinApplyReportRecordedResponse(StrictWireModel):
    operation_id: OperationId
    code: Literal["RECORDED", "DUPLICATE"]
    timestamp: PositiveMilliseconds
    data: EmptyResponseData


type ManualBinApplyReportResponse = (
    ManualBinApplyReportRecordedResponse | UnavailableResponse | ConflictResponse | RejectedResponse
)

_RESPONSE_ADAPTERS = {
    (200, "RECORDED"): TypeAdapter(ManualBinApplyReportRecordedResponse),
    (200, "DUPLICATE"): TypeAdapter(ManualBinApplyReportRecordedResponse),
    (503, "UNAVAILABLE"): TypeAdapter(UnavailableResponse),
    (409, "CONFLICT"): TypeAdapter(ConflictResponse),
    (422, "REJECTED"): TypeAdapter(RejectedResponse),
}


def parse_manual_bin_apply_report_request(
    value: object, *, observation: WmsCallObservation | None = None
) -> ManualBinApplyReportRequest:
    return validate_observed(ManualBinApplyReportRequest, value, observation=observation, side="request")


def parse_manual_bin_apply_report_response(
    status_code: int,
    value: object,
    *,
    request: ManualBinApplyReportRequest | None = None,
    observation: WmsCallObservation | None = None,
) -> ManualBinApplyReportResponse:
    code = value.get("code") if isinstance(value, dict) else None
    adapter = _RESPONSE_ADAPTERS.get((status_code, code)) if isinstance(code, str) else None
    if adapter is None:
        raise observed_contract_error(observation, "HTTP status 与 manual bin apply report response code 不匹配")
    response = validate_observed(adapter, value, observation=observation, side="response")
    if request is not None and response.operation_id != request.operation_id:
        raise observed_contract_error(observation, "响应 operation_id 必须匹配请求", path=("operation_id",))
    return response


__all__ = [
    "MANUAL_BIN_APPLY_REPORT_OPERATION",
    "ManualBinApplyReportRequest",
    "parse_manual_bin_apply_report_request",
    "parse_manual_bin_apply_report_response",
]
