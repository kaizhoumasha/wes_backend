"""PickingTask 计划增量严格 wire；可靠接收由所属 Service 承担。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, model_validator

from src.app.wms_adapter.outbound_picking.wire import BUSINESS_IDENTIFIER_PATTERN
from src.app.wms_adapter.wire_common import (
    NonnegativeMilliseconds,
    OperationId,
    PositiveInteger,
    RackFaceText,
    StrictWireModel,
)
from src.app.wms_diagnostics.observation import WmsCallObservation, validate_observed

PICKING_TASK_PLAN_DELTA_OPERATION = "outbound.picking_task.plan_delta@v1"


class PlanRackFace(StrictWireModel):
    rack_id: Annotated[str, StringConstraints(pattern=BUSINESS_IDENTIFIER_PATTERN)]
    rack_face: RackFaceText


class PlanRackSlot(PlanRackFace):
    type: Literal["RACK_SLOT"]
    slot_id: Annotated[str, StringConstraints(pattern=BUSINESS_IDENTIFIER_PATTERN)]


class PlanDirectPick(StrictWireModel):
    source_locator: PlanRackSlot


class PickingTaskPlanDeltaData(StrictWireModel):
    task_id: Annotated[str, StringConstraints(pattern=BUSINESS_IDENTIFIER_PATTERN)]
    plan_revision: PositiveInteger
    target_rack: PlanRackFace | None = None
    added_bin_source_racks: Annotated[list[PlanRackFace], Field(min_length=1)] | None = None
    added_direct_picks: Annotated[list[PlanDirectPick], Field(min_length=1)] | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null(cls, value: Any) -> Any:
        if isinstance(value, dict):
            for field in ("target_rack", "added_bin_source_racks", "added_direct_picks"):
                if field in value and value[field] is None:
                    raise ValueError(f"{field} 不得为 null；无变化时省略")
        return value

    @model_validator(mode="after")
    def validate_revision_shape(self) -> PickingTaskPlanDeltaData:
        if self.plan_revision == 1:
            if self.target_rack is None:
                raise ValueError("revision 1 必须包含 target_rack")
        elif self.target_rack is not None or not (self.added_bin_source_racks or self.added_direct_picks):
            raise ValueError("后续 revision 禁止 target_rack 且必须新增来源")
        return self


class PickingTaskPlanDeltaEvent(StrictWireModel):
    operation_id: OperationId
    operation: Literal["outbound.picking_task.plan_delta@v1"]
    timestamp: NonnegativeMilliseconds
    data: PickingTaskPlanDeltaData


@dataclass(frozen=True, slots=True)
class PickingTaskPlanDeltaInvalidData:
    raw_envelope: dict[str, Any]
    validation_error: ValueError


def parse_picking_task_plan_delta_event(
    value: object, *, observation: WmsCallObservation | None = None
) -> PickingTaskPlanDeltaEvent:
    return validate_observed(PickingTaskPlanDeltaEvent, value, observation=observation, side="request")


def parse_picking_task_plan_delta_receipt(
    value: dict[str, Any], *, observation: WmsCallObservation | None = None
) -> PickingTaskPlanDeltaEvent | PickingTaskPlanDeltaInvalidData:
    try:
        return parse_picking_task_plan_delta_event(value, observation=observation)
    except ValueError as error:
        return PickingTaskPlanDeltaInvalidData(value, error)
