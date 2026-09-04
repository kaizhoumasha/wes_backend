"""WMS 出库 PickingTask operation 的严格线上合同。"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, TypeAdapter, model_validator

from src.app.wms_adapter.wire_common import (
    NonnegativeMilliseconds,
    OperationId,
    PositiveInteger,
    PositiveMilliseconds,
    StrictWireModel,
)

PICKING_TASK_ISSUED_OPERATION = "outbound.picking_task.issued@v1"
PICKING_TASK_PREPARE_OPERATION = "outbound.picking_task.prepare@v1"
BUSINESS_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}$"

InitialQueueRevision = Annotated[int, Field(strict=True, ge=1, le=1)]


class PickingTaskIssuedData(StrictWireModel):
    task_id: Annotated[str, StringConstraints(pattern=BUSINESS_IDENTIFIER_PATTERN)]
    task_type: Literal["MANUAL", "AUTO"]
    queue_revision: InitialQueueRevision
    dispatch_sequence: PositiveInteger
    not_before: NonnegativeMilliseconds | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null_not_before(cls, value: Any) -> Any:
        if isinstance(value, dict) and "not_before" in value and value["not_before"] is None:
            raise ValueError("not_before 有值时不得为 null，否则应省略")
        return value


class PickingTaskIssuedEvent(StrictWireModel):
    operation_id: OperationId
    operation: Literal["outbound.picking_task.issued@v1"]
    timestamp: PositiveMilliseconds
    data: PickingTaskIssuedData


class PickingTaskPrepareData(StrictWireModel):
    task_id: Annotated[str, StringConstraints(pattern=BUSINESS_IDENTIFIER_PATTERN)]
    workline_code: Annotated[str, StringConstraints(pattern=BUSINESS_IDENTIFIER_PATTERN)]


class PickingTaskPrepareRequest(StrictWireModel):
    operation_id: OperationId
    operation: Literal["outbound.picking_task.prepare@v1"]
    timestamp: PositiveMilliseconds
    data: PickingTaskPrepareData


class EmptyResponseData(StrictWireModel):
    pass


class PickingTaskPrepareAcceptedResponse(StrictWireModel):
    operation_id: OperationId
    code: Literal["PREPARE_ACCEPTED"]
    timestamp: PositiveMilliseconds
    data: EmptyResponseData


class PickingTaskPrepareUnavailableResponse(StrictWireModel):
    operation_id: OperationId
    code: Literal["UNAVAILABLE"]
    timestamp: PositiveMilliseconds
    data: EmptyResponseData


class PickingTaskPrepareConflictData(StrictWireModel):
    reason_code: Literal[
        "IDEMPOTENCY_CONFLICT",
        "REVISION_CONFLICT",
        "STATE_CONFLICT",
        "REFERENCE_CONFLICT",
    ]


class PickingTaskPrepareConflictResponse(StrictWireModel):
    operation_id: OperationId
    code: Literal["CONFLICT"]
    timestamp: PositiveMilliseconds
    data: PickingTaskPrepareConflictData


class PickingTaskPrepareRejectedData(StrictWireModel):
    reason_code: Literal["INVALID_ENVELOPE", "UNSUPPORTED_OPERATION", "INVALID_DATA"]
    field_path: Annotated[str, StringConstraints(min_length=1, max_length=256)] | None = None

    @model_validator(mode="after")
    def validate_field_path(self) -> PickingTaskPrepareRejectedData:
        if self.field_path is not None and (self.reason_code != "INVALID_DATA" or not self.field_path.startswith("/")):
            raise ValueError("field_path 只允许用于 INVALID_DATA 且必须是 JSON Pointer")
        return self


class PickingTaskPrepareRejectedResponse(StrictWireModel):
    operation_id: OperationId
    code: Literal["REJECTED"]
    timestamp: PositiveMilliseconds
    data: PickingTaskPrepareRejectedData


type PickingTaskPrepareResponse = (
    PickingTaskPrepareAcceptedResponse
    | PickingTaskPrepareUnavailableResponse
    | PickingTaskPrepareConflictResponse
    | PickingTaskPrepareRejectedResponse
)

_PREPARE_RESPONSE_ADAPTERS = {
    (202, "PREPARE_ACCEPTED"): TypeAdapter(PickingTaskPrepareAcceptedResponse),
    (503, "UNAVAILABLE"): TypeAdapter(PickingTaskPrepareUnavailableResponse),
    (409, "CONFLICT"): TypeAdapter(PickingTaskPrepareConflictResponse),
    (422, "REJECTED"): TypeAdapter(PickingTaskPrepareRejectedResponse),
}


def parse_picking_task_issued_event(value: object) -> PickingTaskIssuedEvent:
    return PickingTaskIssuedEvent.model_validate(value)


def parse_picking_task_prepare_request(value: object) -> PickingTaskPrepareRequest:
    return PickingTaskPrepareRequest.model_validate(value)


def parse_picking_task_prepare_response(status_code: int, value: object) -> PickingTaskPrepareResponse:
    code = value.get("code") if isinstance(value, dict) else None
    adapter = _PREPARE_RESPONSE_ADAPTERS.get((status_code, code))
    if adapter is None:
        raise ValueError("HTTP status 与 prepare response code 不匹配")
    return adapter.validate_python(value)


__all__ = [
    "BUSINESS_IDENTIFIER_PATTERN",
    "PICKING_TASK_ISSUED_OPERATION",
    "PICKING_TASK_PREPARE_OPERATION",
    "PickingTaskIssuedData",
    "PickingTaskIssuedEvent",
    "PickingTaskPrepareRequest",
    "PickingTaskPrepareResponse",
    "parse_picking_task_issued_event",
    "parse_picking_task_prepare_request",
    "parse_picking_task_prepare_response",
]
