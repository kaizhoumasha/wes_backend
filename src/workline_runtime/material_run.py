"""Material flow source of truth for workline runtime."""

from __future__ import annotations

import datetime  # noqa: TC003
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class LifecycleState(str, Enum):
    ACTIVE = "ACTIVE"
    WAITING = "WAITING"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class MaterialRun(BaseModel):
    run_code: str
    material_identity_key: str
    workline_id: int
    current_device_id: int | None = None
    current_device_role: str | None = None
    current_action: str | None = None
    lifecycle_state: LifecycleState = LifecycleState.ACTIVE
    awaiting_command_id: int | None = None
    blocker_id: int | None = None
    wait_reason: str | None = None
    context_json: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime.datetime | None = None
    ended_at: datetime.datetime | None = None


__all__ = ["LifecycleState", "MaterialRun"]
