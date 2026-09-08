"""入站 Bin 批次的严格请求与封闭决定合同。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, TypeAdapter, model_validator

from src.app.wms_adapter.outbound_picking.response_wire import (
    BinBatchNoBatch,
    ConflictResponse,
    RejectedResponse,
    UnavailableResponse,
)
from src.app.wms_adapter.outbound_picking.wire import BUSINESS_IDENTIFIER_PATTERN
from src.app.wms_adapter.wire_common import NonnegativeMilliseconds, OperationId, RackFaceText, StrictWireModel
from src.app.wms_diagnostics.observation import WmsCallObservation, observed_contract_error, validate_observed

BIN_INBOUND_BATCH_OPERATION = "outbound.bin.inbound_batch@v1"
Identifier = Annotated[str, StringConstraints(pattern=BUSINESS_IDENTIFIER_PATTERN)]


class BinInboundBatchData(StrictWireModel):
    task_id: Identifier
    rack_id: Identifier
    rack_face: RackFaceText
    max_bin_count: Annotated[int, Field(ge=1, le=4)]


class BinInboundBatchRequest(StrictWireModel):
    operation_id: OperationId
    operation: Literal["outbound.bin.inbound_batch@v1"]
    timestamp: NonnegativeMilliseconds
    data: BinInboundBatchData


class BinInboundBatchSourceLocator(StrictWireModel):
    type: Literal["RACK_BIN_SLOT"]
    rack_id: Identifier
    rack_face: RackFaceText
    slot_id: Identifier


class BinInboundBatchMember(StrictWireModel):
    bin_code: Identifier
    source_locator: BinInboundBatchSourceLocator


class BinInboundBatchReady(StrictWireModel):
    result: Literal["READY"]
    bins: Annotated[list[BinInboundBatchMember], Field(min_length=1, max_length=4)]

    @model_validator(mode="after")
    def validate_unique_members(self) -> BinInboundBatchReady:
        if len({item.bin_code for item in self.bins}) != len(self.bins):
            raise ValueError("bins 不得重复 bin_code")
        slots = {
            (item.source_locator.rack_id, item.source_locator.rack_face, item.source_locator.slot_id)
            for item in self.bins
        }
        if len(slots) != len(self.bins):
            raise ValueError("bins 不得重复 source_locator")
        return self


class BinInboundBatchRackFaceDone(StrictWireModel):
    result: Literal["RACK_FACE_DONE"]


class BinInboundBatchDecidedResponse(StrictWireModel):
    operation_id: OperationId
    code: Literal["DECIDED"]
    timestamp: NonnegativeMilliseconds
    data: Annotated[BinInboundBatchReady | BinBatchNoBatch | BinInboundBatchRackFaceDone, Field(discriminator="result")]


type BinInboundBatchResponse = (
    BinInboundBatchDecidedResponse | UnavailableResponse | ConflictResponse | RejectedResponse
)

_RESPONSE_ADAPTERS = {
    (200, "DECIDED"): TypeAdapter(BinInboundBatchDecidedResponse),
    (503, "UNAVAILABLE"): TypeAdapter(UnavailableResponse),
    (409, "CONFLICT"): TypeAdapter(ConflictResponse),
    (422, "REJECTED"): TypeAdapter(RejectedResponse),
}


def parse_bin_inbound_batch_request(
    value: object, *, observation: WmsCallObservation | None = None
) -> BinInboundBatchRequest:
    return validate_observed(BinInboundBatchRequest, value, observation=observation, side="request")


def parse_bin_inbound_batch_response(
    status_code: int,
    value: object,
    *,
    request: BinInboundBatchRequest | None = None,
    observation: WmsCallObservation | None = None,
) -> BinInboundBatchResponse:
    code = value.get("code") if isinstance(value, dict) else None
    adapter = _RESPONSE_ADAPTERS.get((status_code, code)) if isinstance(code, str) else None
    if adapter is None:
        raise observed_contract_error(observation, "HTTP status 与 inbound_batch response code 不匹配")
    response = validate_observed(adapter, value, observation=observation, side="response")
    if request is not None:
        if response.operation_id != request.operation_id:
            raise observed_contract_error(
                observation,
                "响应 operation_id 必须匹配请求",
                path=("operation_id",),
                expected_value=request.operation_id,
            )
        if isinstance(response, BinInboundBatchDecidedResponse) and isinstance(response.data, BinInboundBatchReady):
            if len(response.data.bins) > request.data.max_bin_count:
                raise observed_contract_error(observation, "READY Bin 数量超过请求容量")
            if any(
                (item.source_locator.rack_id, item.source_locator.rack_face)
                != (request.data.rack_id, request.data.rack_face)
                for item in response.data.bins
            ):
                raise observed_contract_error(observation, "READY 来源必须匹配请求货架面")
    return response
