"""WMS 出库 PickingTask operation 的严格线上合同。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, TypeAdapter, model_validator

from src.app.wms_adapter.outbound_picking.response_wire import (  # noqa: TC001 -- Pydantic runtime models
    ConflictData,
    RejectedData,
)
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


class RackPosition(StrictWireModel):
    type: Literal["RACK_POSITION"]
    location_code: Annotated[str, StringConstraints(pattern=BUSINESS_IDENTIFIER_PATTERN)]


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


@dataclass(frozen=True, slots=True)
class PickingTaskIssuedInvalidData:
    """可识别身份的拒绝也必须携带完整信封进入可靠接收事务。"""

    raw_envelope: dict[str, Any]
    validation_error: ValueError


def parse_picking_task_issued_receipt(
    value: dict[str, Any],
) -> PickingTaskIssuedEvent | PickingTaskIssuedInvalidData:
    try:
        return parse_picking_task_issued_event(value)
    except ValueError as error:
        return PickingTaskIssuedInvalidData(value, error)


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


class PickingTaskPrepareConflictResponse(StrictWireModel):
    operation_id: OperationId
    code: Literal["CONFLICT"]
    timestamp: PositiveMilliseconds
    data: ConflictData


class PickingTaskPrepareRejectedResponse(StrictWireModel):
    operation_id: OperationId
    code: Literal["REJECTED"]
    timestamp: PositiveMilliseconds
    data: RejectedData


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
    adapter = _PREPARE_RESPONSE_ADAPTERS.get((status_code, code)) if isinstance(code, str) else None
    if adapter is None:
        raise ValueError("HTTP status 与 prepare response code 不匹配")
    return adapter.validate_python(value)


__all__ = [
    "BUSINESS_IDENTIFIER_PATTERN",
    "PICKING_TASK_ISSUED_OPERATION",
    "PICKING_TASK_PREPARE_OPERATION",
    "PickingTaskIssuedData",
    "PickingTaskIssuedEvent",
    "PickingTaskIssuedInvalidData",
    "PickingTaskPrepareRequest",
    "PickingTaskPrepareResponse",
    "parse_picking_task_issued_event",
    "parse_picking_task_issued_receipt",
    "parse_picking_task_prepare_request",
    "parse_picking_task_prepare_response",
]
