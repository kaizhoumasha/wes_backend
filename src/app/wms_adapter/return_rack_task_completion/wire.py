"""WMS 退料货架整架任务完成的严格 wire 合同。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal, Self

from pydantic import ConfigDict, StringConstraints, model_validator

from src.app.wms_adapter.outbound_picking.wire import BUSINESS_IDENTIFIER_PATTERN
from src.app.wms_adapter.wire_common import (
    OperationId,
    PositiveInteger,
    PositiveMilliseconds,
    StrictWireModel,
)
from src.app.wms_diagnostics.observation import WmsCallObservation, validate_observed

RETURN_RACK_TASK_COMPLETED_OPERATION = "outbound.return_rack.task_completed@v1"
Identifier = Annotated[str, StringConstraints(pattern=BUSINESS_IDENTIFIER_PATTERN)]


class ReturnRackTaskCompletedData(StrictWireModel):
    model_config = ConfigDict(extra="forbid")

    task_id: Identifier
    through_plan_revision: PositiveInteger
    rack_id: Identifier
    completed_at: PositiveMilliseconds


class ReturnRackTaskCompletedEvent(StrictWireModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: OperationId
    operation: Literal["outbound.return_rack.task_completed@v1"]
    timestamp: PositiveMilliseconds
    data: ReturnRackTaskCompletedData

    @model_validator(mode="after")
    def validate_completion_time(self) -> Self:
        if self.data.completed_at > self.timestamp:
            raise ValueError("completed_at 不得晚于 timestamp")
        return self


@dataclass(frozen=True, slots=True)
class ReturnRackTaskCompletedInvalidData:
    raw_envelope: dict[str, Any]
    validation_error: ValueError


def parse_return_rack_task_completed_event(
    value: object, *, observation: WmsCallObservation | None = None
) -> ReturnRackTaskCompletedEvent:
    return validate_observed(ReturnRackTaskCompletedEvent, value, observation=observation, side="request")


def parse_return_rack_task_completed_receipt(
    value: dict[str, Any], *, observation: WmsCallObservation | None = None
) -> ReturnRackTaskCompletedEvent | ReturnRackTaskCompletedInvalidData:
    try:
        return parse_return_rack_task_completed_event(value, observation=observation)
    except ValueError as error:
        return ReturnRackTaskCompletedInvalidData(value, error)


__all__ = [
    "RETURN_RACK_TASK_COMPLETED_OPERATION",
    "ReturnRackTaskCompletedEvent",
    "ReturnRackTaskCompletedInvalidData",
    "parse_return_rack_task_completed_event",
    "parse_return_rack_task_completed_receipt",
]
