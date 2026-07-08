"""DeviceRuntimeProjection runtime device-state projection.

设备运行态在 runtime/orchestration 域的持久投影。
Device 表仍保留设备主数据和当前兼容字段；本表面向调度、诊断和运行态查询。
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - SQLModel table fields need the runtime class at import time.
from typing import Any

from sqlalchemy import JSON, CheckConstraint, Column, Index
from sqlmodel import Field

from src.app.runtime.orchestration.execution_session import RUNTIME_SCHEMA
from src.core.mixins.base import BaseMixin


class DeviceRuntimeProjection(BaseMixin, table=True):
    """设备运行态持久投影。"""

    __tablename__ = "device_runtime_projections"
    __schema__ = RUNTIME_SCHEMA
    __table_args__ = (
        Index("ux_wes_runtime_device_runtime_projections_device_code", "device_code", unique=True),
        Index("ix_wes_runtime_device_runtime_projections_device_id", "device_id"),
        Index("ix_wes_runtime_device_runtime_projections_workline_status", "workline_id", "runtime_status"),
        CheckConstraint(
            "runtime_status IN ('IDLE', 'RUNNING', 'ERROR', 'OFFLINE', 'UNKNOWN', 'MAINTENANCE')",
            name="ck_wes_runtime_device_runtime_projections_status",
        ),
        CheckConstraint(
            "concurrency_limit >= 1",
            name="ck_wes_runtime_device_runtime_projections_concurrency_limit",
        ),
        CheckConstraint(
            "in_flight_count >= 0",
            name="ck_wes_runtime_device_runtime_projections_in_flight_count",
        ),
        {"schema": RUNTIME_SCHEMA},
    )

    id: int | None = Field(default=None, primary_key=True)
    device_id: int | None = Field(default=None)
    device_code: str = Field(max_length=100)
    workline_id: int | None = Field(default=None, index=True)
    device_role: str | None = Field(default=None, max_length=50, index=True)
    provider_code: str | None = Field(default=None, max_length=50, index=True)

    runtime_status: str = Field(default="UNKNOWN", max_length=20, index=True)
    current_command_id: int | None = Field(default=None, index=True)
    error_code: str | None = Field(default=None, max_length=80)
    maintenance_mode: bool = Field(default=False)
    last_heartbeat_at: datetime | None = Field(default=None)
    status_observed_at: datetime = Field(description="naive UTC for DB")
    status_valid_until: datetime = Field(description="naive UTC for DB")
    in_flight_count: int = Field(default=0, ge=0)
    concurrency_limit: int = Field(default=1, ge=1)
    evidence_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, default=dict),
        description="运行态投影证据和来源",
    )


__all__ = ["DeviceRuntimeProjection"]
