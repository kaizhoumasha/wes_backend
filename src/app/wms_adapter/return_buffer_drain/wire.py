"""WorkLine 排空货架决定的封闭 wire；复用候选和公共错误合同。"""

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, TypeAdapter, WithJsonSchema, model_validator
from wes_plugin_sdk import ReturnBufferDrainReason

from src.app.wms_adapter.outbound_picking import response_wire
from src.app.wms_adapter.outbound_picking.return_batch_wire import Identifier, ReturnCandidate, ReturnSource
from src.app.wms_adapter.wire_common import (
    UUIDV7_PATTERN,
    NonnegativeMilliseconds,
    OperationId,
    RackFaceText,
    StrictWireModel,
)
from src.app.wms_diagnostics.observation import WmsCallObservation, observed_contract_error, validate_observed

RETURN_BUFFER_DRAIN_OPERATION = "workline.return_buffer.drain_rack_decide@v1"


class DrainWireModel(StrictWireModel):
    model_config = ConfigDict(extra="forbid")


class DrainSource(ReturnSource, DrainWireModel):
    pass


class DrainCandidate(ReturnCandidate, DrainWireModel):
    source: DrainSource


class DrainData(DrainWireModel):
    workline_code: Identifier
    plugin_key: Identifier
    drain_reason: ReturnBufferDrainReason
    return_candidates: Annotated[list[DrainCandidate], Field(min_length=1, max_length=4)]
    previous_operation_id: Annotated[
        OperationId | None, WithJsonSchema({"type": "string", "pattern": UUIDV7_PATTERN})
    ] = None

    @model_validator(mode="after")
    def validate_fifo_and_predecessor(self):
        if "previous_operation_id" in self.model_fields_set and self.previous_operation_id is None:
            raise ValueError("previous_operation_id 不得为 null")
        candidates = self.return_candidates
        if [c.sequence_no for c in candidates] != list(range(1, len(candidates) + 1)):
            raise ValueError("候选必须从 1 连续编号")
        if len({c.bin_code for c in candidates}) != len(candidates):
            raise ValueError("候选 Bin 不得重复")
        return self


class DrainRequest(DrainWireModel):
    operation: Literal["workline.return_buffer.drain_rack_decide@v1"]
    operation_id: OperationId
    timestamp: NonnegativeMilliseconds
    data: DrainData

    @model_validator(mode="after")
    def validate_new_identity(self):
        if self.data.previous_operation_id == self.operation_id:
            raise ValueError("重新求值必须使用新 operation_id")
        return self


class DrainReady(DrainWireModel):
    result: Literal["READY"]
    rack_id: Identifier
    rack_face: RackFaceText


class DrainWait(DrainWireModel):
    result: Literal["WAIT"]
    reason_code: Literal["NO_DRAIN_RACK_AVAILABLE"]
    retry_after_ms: Annotated[int, Field(ge=1, le=60000)]


class DrainDecidedResponse(DrainWireModel):
    operation_id: OperationId
    code: Literal["DECIDED"]
    timestamp: NonnegativeMilliseconds
    data: Annotated[DrainReady | DrainWait, Field(discriminator="result")]


class DrainEmptyData(response_wire.EmptyResponseData, DrainWireModel):
    pass


class DrainConflictData(response_wire.ConflictData, DrainWireModel):
    pass


class DrainRejectedData(response_wire.RejectedData, DrainWireModel):
    pass


class DrainUnavailableResponse(response_wire.UnavailableResponse, DrainWireModel):
    data: DrainEmptyData


class DrainConflictResponse(response_wire.ConflictResponse, DrainWireModel):
    data: DrainConflictData


class DrainRejectedResponse(response_wire.RejectedResponse, DrainWireModel):
    data: DrainRejectedData


type DrainResponse = DrainDecidedResponse | DrainUnavailableResponse | DrainConflictResponse | DrainRejectedResponse
_RESPONSE_ADAPTERS = {
    (200, "DECIDED"): TypeAdapter(DrainDecidedResponse),
    (503, "UNAVAILABLE"): TypeAdapter(DrainUnavailableResponse),
    (409, "CONFLICT"): TypeAdapter(DrainConflictResponse),
    (422, "REJECTED"): TypeAdapter(DrainRejectedResponse),
}


def parse_request(value: object, *, observation: WmsCallObservation | None = None) -> DrainRequest:
    return validate_observed(DrainRequest, value, observation=observation, side="request")


def parse_response(
    status_code: int,
    value: object,
    *,
    request: DrainRequest | None = None,
    observation: WmsCallObservation | None = None,
) -> DrainResponse:
    code = value.get("code") if isinstance(value, dict) else None
    adapter = _RESPONSE_ADAPTERS.get((status_code, code)) if isinstance(code, str) else None
    if adapter is None:
        raise observed_contract_error(observation, "HTTP status 与 drain code 不匹配")
    response = validate_observed(adapter, value, observation=observation, side="response")
    if request is not None and response.operation_id != request.operation_id:
        raise observed_contract_error(
            observation, "响应 identity 不匹配", path=("operation_id",), expected_value=request.operation_id
        )
    return response
