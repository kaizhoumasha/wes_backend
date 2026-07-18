"""DeviceCommand OUTBOX_ASYNC typed input/output。"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints, model_validator

DeviceFactVersion = Annotated[str, StringConstraints(pattern=r"^device:v\d+$")]


class DeviceCommandWritePrecondition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_available: StrictBool


class DeviceCommandWriteAdmission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    precondition: DeviceCommandWritePrecondition
    fact_version: DeviceFactVersion


class DeviceCommandWriteInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    device_role: str | None = Field(default=None, min_length=1, max_length=100)
    target_device_id: int | None = Field(default=None, gt=0)
    action: str = Field(min_length=1, max_length=50)
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(default=5, ge=1, le=10)
    timeout_ms: int = Field(default=30000, ge=1000, le=300000)
    command_code: str | None = Field(default=None, min_length=1, max_length=100)
    result_policy: Literal["COMMAND_RESULT"]

    @model_validator(mode="after")
    def require_one_target(self) -> DeviceCommandWriteInput:
        if (self.device_role is None) == (self.target_device_id is None):
            raise ValueError("exactly one of device_role or target_device_id is required")
        return self


class DeviceCommandWriteOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted: bool
    command_code: str
    dispatch_key: str


__all__ = [
    "DeviceCommandWriteAdmission",
    "DeviceCommandWriteInput",
    "DeviceCommandWriteOutput",
    "DeviceCommandWritePrecondition",
]
