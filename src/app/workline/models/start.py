"""WorkLine START API contract."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic requires runtime type access
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WorkLineStartRequest(BaseModel):
    """Stable identity for one WorkLine START attempt."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=100)

    @field_validator("request_id", mode="before")
    @classmethod
    def normalize_request_id(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class WorkLineStartResponse(BaseModel):
    """Frozen Epoch identity and the current WorkLine projection."""

    line_run_epoch_id: int
    epoch_code: str
    workline_id: int
    plugin_key: str
    plugin_version: str
    flow_mode: str
    epoch_status: Literal["ACTIVE", "CLOSED"]
    epoch_started_at: datetime
    epoch_closed_at: datetime | None
    current_workline_runtime_status: str | None
    created: bool


class WorkLineStartErrorResponse(BaseModel):
    """Stable machine-readable START rejection."""

    reason: Literal[
        "WORKLINE_NOT_FOUND",
        "INVALID_STATE",
        "CONFIGURATION_INVALID",
        "IDEMPOTENCY_CONFLICT",
        "SERVICE_UNAVAILABLE",
    ]


__all__ = ["WorkLineStartErrorResponse", "WorkLineStartRequest", "WorkLineStartResponse"]
