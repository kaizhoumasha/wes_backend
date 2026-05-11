"""Append-only runtime facts used for trace, projections, metrics, and replay."""

from __future__ import annotations

import datetime  # noqa: TC003
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.utils.timezone import timezone


class RuntimeEventType(str, Enum):
    MATERIAL_ENTERED_DEVICE = "MATERIAL_ENTERED_DEVICE"
    MATERIAL_LEFT_DEVICE = "MATERIAL_LEFT_DEVICE"
    COMMAND_CREATED = "COMMAND_CREATED"
    COMMAND_ACKED = "COMMAND_ACKED"
    COMMAND_SUCCEEDED = "COMMAND_SUCCEEDED"
    COMMAND_FAILED = "COMMAND_FAILED"
    COMMAND_TIMEOUT = "COMMAND_TIMEOUT"
    PLUGIN_DECISION_MADE = "PLUGIN_DECISION_MADE"
    PROCESS_BLOCKED = "PROCESS_BLOCKED"
    PROCESS_UNBLOCKED = "PROCESS_UNBLOCKED"
    PROCESS_COMPLETED = "PROCESS_COMPLETED"
    PROCESS_FAILED = "PROCESS_FAILED"
    DEVICE_STATUS_CHANGED = "DEVICE_STATUS_CHANGED"


class RuntimeEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: RuntimeEventType
    trace_id: str
    material_run_id: int | None = None
    material_identity_key: str | None = None
    workline_id: int
    device_id: int | None = None
    device_role: str | None = None
    plugin_key: str | None = None
    action: str | None = None
    command_id: int | None = None
    occurred_at: datetime.datetime = Field(default_factory=timezone.now_for_db)
    duration_ms: int | None = None
    result: str | None = None
    reason_code: str | None = None
    failure_domain: str | None = None
    owner: str | None = None
    payload_json: dict[str, Any] = Field(default_factory=dict)


__all__ = ["RuntimeEvent", "RuntimeEventType"]
