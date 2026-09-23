"""WMS 退料货架直接取料完成的严格 wire 合同。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal, Self

from pydantic import ConfigDict, StringConstraints, model_validator

from src.app.wms_adapter.outbound_picking.wire import BUSINESS_IDENTIFIER_PATTERN
from src.app.wms_adapter.wire_common import (
    OperationId,
    PositiveInteger,
    PositiveMilliseconds,
    RackFaceText,
    StrictWireModel,
)
from src.app.wms_diagnostics.observation import WmsCallObservation, validate_observed

MANUAL_RACK_DIRECT_PICK_OPERATION = "outbound.manual_rack.direct_pick_completed@v1"
Identifier = Annotated[str, StringConstraints(pattern=BUSINESS_IDENTIFIER_PATTERN)]


class ManualRackDirectPickData(StrictWireModel):
    model_config = ConfigDict(extra="forbid")

    task_id: Identifier
    plan_revision: PositiveInteger
    rack_id: Identifier
    rack_face: RackFaceText
    completed_at: PositiveMilliseconds


class ManualRackDirectPickEvent(StrictWireModel):
    operation_id: OperationId
    operation: Literal["outbound.manual_rack.direct_pick_completed@v1"]
    timestamp: PositiveMilliseconds
    data: ManualRackDirectPickData

    @model_validator(mode="after")
    def validate_completion_time(self) -> Self:
        if self.data.completed_at > self.timestamp:
            raise ValueError("completed_at 不得晚于 timestamp")
        return self


@dataclass(frozen=True, slots=True)
class ManualRackDirectPickInvalidData:
    raw_envelope: dict[str, Any]
    validation_error: ValueError


def parse_manual_rack_direct_pick_event(
    value: object, *, observation: WmsCallObservation | None = None
) -> ManualRackDirectPickEvent:
    return validate_observed(ManualRackDirectPickEvent, value, observation=observation, side="request")


def parse_manual_rack_direct_pick_receipt(
    value: dict[str, Any], *, observation: WmsCallObservation | None = None
) -> ManualRackDirectPickEvent | ManualRackDirectPickInvalidData:
    try:
        return parse_manual_rack_direct_pick_event(value, observation=observation)
    except ValueError as error:
        return ManualRackDirectPickInvalidData(value, error)


__all__ = [
    "MANUAL_RACK_DIRECT_PICK_OPERATION",
    "ManualRackDirectPickEvent",
    "ManualRackDirectPickInvalidData",
    "parse_manual_rack_direct_pick_event",
    "parse_manual_rack_direct_pick_receipt",
]
