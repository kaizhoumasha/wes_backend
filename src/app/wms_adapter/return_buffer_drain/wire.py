"""WorkLine 排空货架决定的封闭 wire；复用候选和公共错误合同。"""

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, TypeAdapter, model_validator

from src.app.wms_adapter.outbound_picking import response_wire
from src.app.wms_adapter.outbound_picking.return_batch_wire import Identifier
from src.app.wms_adapter.wire_common import (
    NonnegativeMilliseconds,
    OperationId,
    PositiveInteger,
    RackFaceText,
    StrictWireModel,
)
from src.app.wms_diagnostics.observation import WmsCallObservation, observed_contract_error, validate_observed

RETURN_BUFFER_DRAIN_OPERATION = "workline.return_buffer.drain_rack_decide@v1"


class DrainWireModel(StrictWireModel):
    model_config = ConfigDict(extra="forbid")


class DrainData(DrainWireModel):
    workline_code: Identifier
    required_slot_count: PositiveInteger


class DrainRequest(DrainWireModel):
    operation: Literal["workline.return_buffer.drain_rack_decide@v1"]
    operation_id: OperationId
    timestamp: NonnegativeMilliseconds
    data: DrainData


class DrainRack(DrainWireModel):
    rack_id: Identifier
    rack_faces: Annotated[list[RackFaceText], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_faces(self):
        if len(self.rack_faces) != len(set(self.rack_faces)):
            raise ValueError("同一货架面不得重复")
        return self


class DrainReady(DrainWireModel):
    result: Literal["READY"]
    racks: Annotated[list[DrainRack], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_racks(self):
        rack_ids = [rack.rack_id for rack in self.racks]
        if len(rack_ids) != len(set(rack_ids)):
            raise ValueError("货架不得重复")
        return self


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
    if request is not None and isinstance(response, DrainDecidedResponse) and isinstance(response.data, DrainReady):
        face_count = sum(len(rack.rack_faces) for rack in response.data.racks)
        if face_count > request.data.required_slot_count:
            raise observed_contract_error(observation, "返回货架面数量超过 required_slot_count")
    return response
