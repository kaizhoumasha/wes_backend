"""WorkLine 乐观锁启动合同。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class WorkLineStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(strict=True, ge=0)


class WorkLineStartResponse(BaseModel):
    workline_id: int
    version: int
    plugin_key: str
    plugin_version: str
    flow_mode: str | None
    is_active: bool


class WorkLineStartErrorResponse(BaseModel):
    reason: Literal[
        "WORKLINE_NOT_FOUND", "INVALID_STATE", "CONFIGURATION_INVALID", "VERSION_CONFLICT", "SERVICE_UNAVAILABLE"
    ]
