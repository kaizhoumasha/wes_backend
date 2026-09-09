"""WMS 人工工作位 Bin 完成决定的严格 wire 合同。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal, Self

from pydantic import StringConstraints, model_validator

from src.app.wms_adapter.outbound_picking.wire import BUSINESS_IDENTIFIER_PATTERN
from src.app.wms_adapter.wire_common import OperationId, PositiveMilliseconds, StrictWireModel
from src.app.wms_diagnostics.observation import WmsCallObservation, validate_observed

MANUAL_BIN_COMPLETED_OPERATION = "outbound.manual_bin.work_completed@v1"
Identifier = Annotated[str, StringConstraints(pattern=BUSINESS_IDENTIFIER_PATTERN)]


class ManualBinCompletedData(StrictWireModel):
    task_id: Identifier
    bin_code: Identifier
    result: Literal["NORMAL", "NG"]
    completed_at: PositiveMilliseconds


class ManualBinCompletedEvent(StrictWireModel):
    operation_id: OperationId
    operation: Literal["outbound.manual_bin.work_completed@v1"]
    timestamp: PositiveMilliseconds
    data: ManualBinCompletedData

    @model_validator(mode="after")
    def validate_completion_time(self) -> Self:
        if self.data.completed_at > self.timestamp:
            raise ValueError("completed_at 不得晚于 timestamp")
        return self


@dataclass(frozen=True, slots=True)
class ManualBinCompletedInvalidData:
    raw_envelope: dict[str, Any]
    validation_error: ValueError


def parse_manual_bin_completed_event(
    value: object, *, observation: WmsCallObservation | None = None
) -> ManualBinCompletedEvent:
    return validate_observed(ManualBinCompletedEvent, value, observation=observation, side="request")


def parse_manual_bin_completed_receipt(
    value: dict[str, Any], *, observation: WmsCallObservation | None = None
) -> ManualBinCompletedEvent | ManualBinCompletedInvalidData:
    try:
        return parse_manual_bin_completed_event(value, observation=observation)
    except ValueError as error:
        return ManualBinCompletedInvalidData(value, error)


__all__ = [
    "MANUAL_BIN_COMPLETED_OPERATION",
    "ManualBinCompletedEvent",
    "ManualBinCompletedInvalidData",
    "parse_manual_bin_completed_event",
    "parse_manual_bin_completed_receipt",
]
