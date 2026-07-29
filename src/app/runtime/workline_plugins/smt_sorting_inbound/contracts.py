"""SMT 分拣入库的 config、state、route input 与 facts。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, PositiveInt, StringConstraints, model_validator

from src.app.runtime.workline_plugins.dispatcher import PinnedPluginSnapshot  # noqa: TC001
from src.app.wms_integration.ports.fulfillment_operations import MOVE_BINS_FROM_CONVEYOR_EXIT

StableString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


class ConveyorEntryQueueConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    code: StableString
    role: Literal["ENTRY"]
    capacity: PositiveInt
    order_policy: Literal["FIFO"]


class ReturnQueueConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    code: StableString
    role: Literal["RETURN_QUEUE"]
    order_policy: Literal["FIFO"]


class SmtSortingInboundConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    provider_profile: StableString
    source_arm_role: Literal["SORTING_SOURCE_ARM"] = "SORTING_SOURCE_ARM"
    ctu_basket_capacity: PositiveInt
    conveyor_entry_queue: ConveyorEntryQueueConfig
    return_queue: ReturnQueueConfig

    @model_validator(mode="after")
    def validate_factory_queue_constraints(self) -> SmtSortingInboundConfig:
        if self.conveyor_entry_queue.code == self.return_queue.code:
            raise ValueError("conveyor_entry_queue and return_queue code must differ")

        # 候选上限由 typed WMS operation definition 单点定义，factory config 不复制协议常量。
        max_candidate_count = MOVE_BINS_FROM_CONVEYOR_EXIT.max_candidate_count
        if max_candidate_count is None or self.ctu_basket_capacity > max_candidate_count:
            raise ValueError("ctu_basket_capacity exceeds E13 max_candidate_count")
        return self


class SmtSortingInboundState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    phase: Literal["WAITING_SOURCE_PICK", "WAITING_SCAN"] = "WAITING_SOURCE_PICK"
    current_correlation: StableString | None = None


class SourcePickRequestInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    route: Literal["SOURCE_PICK_REQUESTED"] = "SOURCE_PICK_REQUESTED"
    handoff_demand_id: int
    handoff_source_item_id: int
    claim_attempt_no: int
    source_pick_request_event_id: StableString


class SmtSortingInboundFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    correlation_matches: bool = True
    route_diagnostic: StableString | None = None
    binding_snapshot: PinnedPluginSnapshot
    source_arm_device_id: int | None = None
    source_arm_device_version: int | None = None


__all__ = [
    "ConveyorEntryQueueConfig",
    "ReturnQueueConfig",
    "SmtSortingInboundConfig",
    "SmtSortingInboundFacts",
    "SmtSortingInboundState",
    "SourcePickRequestInput",
]
