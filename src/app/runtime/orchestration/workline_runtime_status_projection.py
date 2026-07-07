"""WorkLine runtime status native projection."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from enum import Enum
from typing import Any, ClassVar

from sqlalchemy import JSON, CheckConstraint, Column, Index
from sqlmodel import Field

from src.app.runtime.orchestration.execution_session import RUNTIME_SCHEMA
from src.core.mixins.base import BaseMixin


class WorkLineRuntimeStatus(str, Enum):
    """Runtime-owned WorkLine operational status."""

    READY = "READY"
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    ESTOPPED = "ESTOPPED"
    RECONCILING = "RECONCILING"


class WorklineRuntimeStatusProjection(BaseMixin, table=True):
    """Runtime/orchestration-owned WorkLine status projection."""

    __tablename__ = "workline_runtime_status_projections"
    __schema__ = RUNTIME_SCHEMA
    __table_args__: ClassVar[tuple[Any, ...]] = (
        Index(
            "ux_wrt_status_proj_workline",
            "workline_id",
            unique=True,
        ),
        Index(
            "ix_wrt_status_proj_status",
            "runtime_status",
        ),
        Index(
            "ix_wrt_status_proj_safety_incident",
            "active_safety_incident_id",
        ),
        CheckConstraint(
            "runtime_status IN ('READY', 'STOPPED', 'STARTING', 'ESTOPPED', 'RECONCILING')",
            name="ck_wrt_status_proj_status",
        ),
        {"schema": RUNTIME_SCHEMA},
    )

    id: int | None = Field(default=None, primary_key=True)
    workline_id: int = Field(index=True, description="WorkLine configuration id")
    runtime_status: str = Field(default=WorkLineRuntimeStatus.STOPPED.value, max_length=20, index=True)
    source: str = Field(default="runtime/orchestration", max_length=100)
    stopped_at: datetime | None = Field(default=None, description="naive UTC for DB")
    stopped_reason: str | None = Field(default=None, max_length=200)
    resumed_at: datetime | None = Field(default=None, description="naive UTC for DB")
    active_safety_incident_id: int | None = Field(default=None, index=True)
    evidence_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, default=dict),
    )


__all__ = [
    "WorkLineRuntimeStatus",
    "WorklineRuntimeStatusProjection",
]
