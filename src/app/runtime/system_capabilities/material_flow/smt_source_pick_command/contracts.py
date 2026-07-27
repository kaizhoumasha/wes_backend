"""SMT source-pick command OUTBOX_ASYNC typed contract。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.app.runtime.system_capabilities.device.device_command_write.contracts import (
    DeviceCommandWriteAdmission,
)


class SmtSourcePickCommandPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    handoff_demand_id: int = Field(gt=0)
    handoff_source_item_id: int = Field(gt=0)
    claim_attempt_no: int = Field(ge=0)
    source_pick_request_event_id: str = Field(min_length=1, max_length=160)


class SmtSourcePickCommandInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target_device_id: int = Field(gt=0)
    action: Literal["SORTING_SOURCE_PICK"]
    payload: SmtSourcePickCommandPayload
    priority: int = Field(default=5, ge=1, le=10)
    timeout_ms: int = Field(default=30000, ge=1000, le=300000)
    command_code: str = Field(min_length=1, max_length=100)
    result_policy: Literal["COMMAND_RESULT"]


class SmtSourcePickCommandOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted: bool
    command_code: str
    dispatch_key: str


SmtSourcePickCommandAdmission = DeviceCommandWriteAdmission

__all__ = [
    "SmtSourcePickCommandAdmission",
    "SmtSourcePickCommandInput",
    "SmtSourcePickCommandOutput",
    "SmtSourcePickCommandPayload",
]
