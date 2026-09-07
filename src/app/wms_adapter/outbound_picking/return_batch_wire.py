"""退箱批次：封闭 DTO 与请求 FIFO 前缀关联校验。"""

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

BIN_RETURN_BATCH_OPERATION = "outbound.bin.return_batch@v1"
Identifier = Annotated[str, StringConstraints(pattern=BUSINESS_IDENTIFIER_PATTERN)]


class ReturnSource(StrictWireModel):
    type: Literal["HANDOFF_POSITION"]
    location_code: Identifier


class ReturnCandidate(StrictWireModel):
    sequence_no: Annotated[int, Field(ge=1, le=4)]
    bin_code: Identifier
    source: ReturnSource


class BinReturnBatchData(StrictWireModel):
    workline_code: Identifier
    line_run_epoch_id: Identifier
    rack_id: Identifier
    rack_face: RackFaceText
    return_candidates: Annotated[list[ReturnCandidate], Field(min_length=1, max_length=4)]

    @model_validator(mode="after")
    def validate_fifo(self):
        if [c.sequence_no for c in self.return_candidates] != list(range(1, len(self.return_candidates) + 1)):
            raise ValueError("候选必须从 1 连续编号")
        if len({c.bin_code for c in self.return_candidates}) != len(self.return_candidates):
            raise ValueError("候选 Bin 不得重复")
        return self


class BinReturnBatchRequest(StrictWireModel):
    operation_id: OperationId
    operation: Literal["outbound.bin.return_batch@v1"]
    timestamp: NonnegativeMilliseconds
    data: BinReturnBatchData


class ReturnTarget(StrictWireModel):
    type: Literal["RACK_BIN_SLOT"]
    rack_id: Identifier
    rack_face: RackFaceText
    slot_id: Identifier


class ReturnMove(StrictWireModel):
    sequence_no: Annotated[int, Field(ge=1, le=4)]
    bin_code: Identifier
    target: ReturnTarget


class BinReturnBatchReady(StrictWireModel):
    result: Literal["READY"]
    moves: Annotated[list[ReturnMove], Field(min_length=1, max_length=4)]

    @model_validator(mode="after")
    def validate_unique_moves(self):
        if [m.sequence_no for m in self.moves] != list(range(1, len(self.moves) + 1)):
            raise ValueError("moves 必须从队首连续编号")
        if len({m.bin_code for m in self.moves}) != len(self.moves):
            raise ValueError("moves Bin 不得重复")
        if len({(m.target.rack_id, m.target.rack_face, m.target.slot_id) for m in self.moves}) != len(self.moves):
            raise ValueError("目标储位不得重复")
        return self


class BinReturnBatchDecidedResponse(StrictWireModel):
    operation_id: OperationId
    code: Literal["DECIDED"]
    timestamp: NonnegativeMilliseconds
    data: Annotated[BinReturnBatchReady | BinBatchNoBatch, Field(discriminator="result")]


type BinReturnBatchResponse = BinReturnBatchDecidedResponse | UnavailableResponse | ConflictResponse | RejectedResponse
_RESPONSE_ADAPTERS = {
    (200, "DECIDED"): TypeAdapter(BinReturnBatchDecidedResponse),
    (503, "UNAVAILABLE"): TypeAdapter(UnavailableResponse),
    (409, "CONFLICT"): TypeAdapter(ConflictResponse),
    (422, "REJECTED"): TypeAdapter(RejectedResponse),
}


def parse_bin_return_batch_request(value: object) -> BinReturnBatchRequest:
    return BinReturnBatchRequest.model_validate(value)


def parse_bin_return_batch_response(
    status_code: int, value: object, *, request: BinReturnBatchRequest | None = None
) -> BinReturnBatchResponse:
    code = value.get("code") if isinstance(value, dict) else None
    adapter = _RESPONSE_ADAPTERS.get((status_code, code)) if isinstance(code, str) else None
    if adapter is None:
        raise ValueError("HTTP status 与 return_batch code 不匹配")
    response = adapter.validate_python(value)
    if request is not None:
        if response.operation_id != request.operation_id:
            raise ValueError("响应 identity 不匹配")
        if response.code == "DECIDED" and isinstance(response.data, BinReturnBatchReady):
            moves = response.data.moves
            candidates = request.data.return_candidates[: len(moves)]
            if [(m.sequence_no, m.bin_code) for m in moves] != [(c.sequence_no, c.bin_code) for c in candidates]:
                raise ValueError("READY 必须匹配候选 FIFO 前缀")
            if any(
                (m.target.rack_id, m.target.rack_face) != (request.data.rack_id, request.data.rack_face) for m in moves
            ):
                raise ValueError("READY 目标必须匹配冻结货架面")
    return response
