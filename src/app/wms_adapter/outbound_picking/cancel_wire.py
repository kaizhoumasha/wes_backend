"""PickingTask 整单或计划成员取消的严格入站合同。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, StringConstraints, model_validator

from src.app.wms_adapter.outbound_picking.wire import BUSINESS_IDENTIFIER_PATTERN
from src.app.wms_adapter.wire_common import NonnegativeMilliseconds, OperationId, RackFaceText, StrictWireModel
from src.app.wms_diagnostics.observation import WmsCallObservation, validate_observed

PICKING_TASK_CANCEL_OPERATION = "outbound.picking_task.cancel@v1"
BusinessIdentifier = Annotated[str, StringConstraints(pattern=BUSINESS_IDENTIFIER_PATTERN)]


class CancelBinSourceRack(StrictWireModel):
    rack_id: BusinessIdentifier
    rack_faces: Annotated[list[RackFaceText], Field(min_length=1)]

    @model_validator(mode="after")
    def reject_duplicate_faces(self) -> CancelBinSourceRack:
        if len(set(self.rack_faces)) != len(self.rack_faces):
            raise ValueError("rack_faces 不得重复")
        return self


class CancelDirectPickSource(StrictWireModel):
    rack_id: BusinessIdentifier
    rack_face: RackFaceText
    slot_ids: Annotated[list[BusinessIdentifier], Field(min_length=1)]

    @model_validator(mode="after")
    def reject_duplicate_slots(self) -> CancelDirectPickSource:
        if len(set(self.slot_ids)) != len(self.slot_ids):
            raise ValueError("slot_ids 不得重复")
        return self


class PickingTaskCancelTaskData(StrictWireModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    task_id: BusinessIdentifier
    cancel_scope: Literal["TASK"]


class PickingTaskCancelMembersData(StrictWireModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    task_id: BusinessIdentifier
    cancel_scope: Literal["PLAN_MEMBERS"]
    bin_source_racks: list[CancelBinSourceRack] | None = None
    direct_pick_sources: list[CancelDirectPickSource] | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null(cls, value: Any) -> Any:
        if isinstance(value, dict):
            for field in ("bin_source_racks", "direct_pick_sources"):
                if field in value and value[field] is None:
                    raise ValueError(f"{field} 不得为 null；无选择器时省略")
        return value

    @model_validator(mode="after")
    def validate_selectors(self) -> PickingTaskCancelMembersData:
        if not self.bin_source_racks and not self.direct_pick_sources:
            raise ValueError("PLAN_MEMBERS 至少需要一种非空选择器")
        rack_ids = [item.rack_id for item in self.bin_source_racks or ()]
        if len(set(rack_ids)) != len(rack_ids):
            raise ValueError("bin_source_racks.rack_id 不得重复")
        direct_faces = [(item.rack_id, item.rack_face) for item in self.direct_pick_sources or ()]
        if len(set(direct_faces)) != len(direct_faces):
            raise ValueError("direct_pick_sources 的 rack_id + rack_face 不得重复")
        return self


type PickingTaskCancelData = PickingTaskCancelTaskData | PickingTaskCancelMembersData


class PickingTaskCancelEvent(StrictWireModel):
    operation_id: OperationId
    operation: Literal["outbound.picking_task.cancel@v1"]
    timestamp: NonnegativeMilliseconds
    data: Annotated[PickingTaskCancelData, Field(discriminator="cancel_scope")]


@dataclass(frozen=True, slots=True)
class PickingTaskCancelInvalidData:
    raw_envelope: dict[str, Any]
    validation_error: ValueError


def parse_picking_task_cancel_event(
    value: object, *, observation: WmsCallObservation | None = None
) -> PickingTaskCancelEvent:
    return validate_observed(PickingTaskCancelEvent, value, observation=observation, side="request")


def parse_picking_task_cancel_receipt(
    value: dict[str, Any], *, observation: WmsCallObservation | None = None
) -> PickingTaskCancelEvent | PickingTaskCancelInvalidData:
    try:
        return parse_picking_task_cancel_event(value, observation=observation)
    except ValueError as error:
        return PickingTaskCancelInvalidData(value, error)


__all__ = [
    "PICKING_TASK_CANCEL_OPERATION",
    "PickingTaskCancelEvent",
    "PickingTaskCancelInvalidData",
    "PickingTaskCancelMembersData",
    "PickingTaskCancelTaskData",
    "parse_picking_task_cancel_event",
    "parse_picking_task_cancel_receipt",
]
