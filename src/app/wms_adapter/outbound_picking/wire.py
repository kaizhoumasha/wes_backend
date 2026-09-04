"""WMS 发布出库 PickingTask 的严格线上合同。"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, model_validator

from src.app.wms_adapter.wire_common import (
    NonnegativeMilliseconds,
    OperationId,
    PositiveInteger,
    PositiveMilliseconds,
    StrictWireModel,
)

PICKING_TASK_ISSUED_OPERATION = "outbound.picking_task.issued@v1"
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


def parse_picking_task_issued_event(value: object) -> PickingTaskIssuedEvent:
    return PickingTaskIssuedEvent.model_validate(value)


__all__ = [
    "BUSINESS_IDENTIFIER_PATTERN",
    "PICKING_TASK_ISSUED_OPERATION",
    "PickingTaskIssuedData",
    "PickingTaskIssuedEvent",
    "parse_picking_task_issued_event",
]
