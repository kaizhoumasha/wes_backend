"""PickingTask 队列更新严格 wire；业务版本及状态由事务服务校验。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, model_validator

from src.app.wms_adapter.outbound_picking.wire import BUSINESS_IDENTIFIER_PATTERN
from src.app.wms_adapter.wire_common import (
    NonnegativeMilliseconds,
    OperationId,
    PositiveInteger,
    StrictWireModel,
)
from src.app.wms_diagnostics.observation import WmsCallObservation, validate_observed

PICKING_TASK_QUEUE_CHANGED_OPERATION = "outbound.picking_task.queue_changed@v1"


class PickingTaskQueueChangedData(StrictWireModel):
    task_id: Annotated[str, StringConstraints(pattern=BUSINESS_IDENTIFIER_PATTERN)]
    queue_revision: Annotated[PositiveInteger, Field(ge=2)]
    dispatch_sequence: PositiveInteger | None = None
    not_before: NonnegativeMilliseconds | None = None

    @model_validator(mode="before")
    @classmethod
    def validate_update_presence(cls, value: Any) -> Any:
        if isinstance(value, dict):
            updates = {"dispatch_sequence", "not_before"} & value.keys()
            if not updates:
                raise ValueError("队列更新必须提供 dispatch_sequence 或 not_before")
            if any(value[name] is None for name in updates):
                raise ValueError("队列字段不得为 null；无变化时省略")
        return value


class PickingTaskQueueChangedEvent(StrictWireModel):
    operation_id: OperationId
    operation: Literal["outbound.picking_task.queue_changed@v1"]
    timestamp: NonnegativeMilliseconds
    data: PickingTaskQueueChangedData


@dataclass(frozen=True, slots=True)
class PickingTaskQueueChangedInvalidData:
    raw_envelope: dict[str, Any]
    validation_error: ValueError


def parse_picking_task_queue_changed_event(
    value: object, *, observation: WmsCallObservation | None = None
) -> PickingTaskQueueChangedEvent:
    return validate_observed(PickingTaskQueueChangedEvent, value, observation=observation, side="request")


def parse_picking_task_queue_changed_receipt(
    value: dict[str, Any], *, observation: WmsCallObservation | None = None
) -> PickingTaskQueueChangedEvent | PickingTaskQueueChangedInvalidData:
    try:
        return parse_picking_task_queue_changed_event(value, observation=observation)
    except ValueError as error:
        return PickingTaskQueueChangedInvalidData(value, error)
