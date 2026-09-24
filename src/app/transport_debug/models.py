"""Transport 自动联调轮次的冻结配置和步骤模型。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - SQLModel resolves this annotation at runtime
from typing import Any

from sqlalchemy import JSON, BigInteger, CheckConstraint, Column, Index, Text, UniqueConstraint, text
from sqlmodel import Field

from src.core.mixins.base import BaseMixin
from src.database.schema_conf import SchemaType

BIZ_SCHEMA = SchemaType.BIZ.value

_DEBUG_RUN_STATUS_CHECK = "status IN ('RUNNING', 'NEEDS_ATTENTION', 'COMPLETED', 'FAILED', 'ABORTED')"
_DEBUG_RUN_PHASE_CHECK = (
    "current_phase IN ('RACK_TO_STATION', 'BINS_TO_INFEED', 'WAIT_SCAN12', 'BINS_TO_RACK', "
    "'ROTATE_TO_NEXT_FACE', 'RACK_TO_STORAGE')"
)
_DEBUG_RUN_STEP_STATUS_CHECK = "status IN ('PENDING', 'WAITING', 'SUCCEEDED', 'FAILED', 'NEEDS_ATTENTION')"
_DEBUG_RUN_STEP_PHASE_CHECK = (
    "phase IN ('RACK_TO_STATION', 'BINS_TO_INFEED', 'WAIT_SCAN12', 'BINS_TO_RACK', "
    "'ROTATE_TO_NEXT_FACE', 'RACK_TO_STORAGE')"
)


class TransportDebugRun(BaseMixin, table=True):
    """自动联调轮次的冻结配置与可恢复游标。"""

    __tablename__ = "transport_debug_runs"  # pyright: ignore[reportAssignmentType]
    __schema__ = BIZ_SCHEMA
    __table_args__ = (
        CheckConstraint(_DEBUG_RUN_STATUS_CHECK, name="transport_debug_run_status_valid"),
        CheckConstraint(_DEBUG_RUN_PHASE_CHECK, name="transport_debug_run_phase_valid"),
        CheckConstraint(
            "(status IN ('RUNNING', 'NEEDS_ATTENTION') AND active_scope IS NOT NULL "
            "AND active_scope = 'GLOBAL') OR "
            "(status IN ('COMPLETED', 'FAILED', 'ABORTED') AND active_scope IS NULL)",
            name="transport_debug_run_status_scope_consistent",
        ),
        CheckConstraint(
            "(claim_token IS NULL) = (claim_until IS NULL)",
            name="transport_debug_run_claim_complete",
        ),
        CheckConstraint(
            "claim_token IS NULL OR (active_scope IS NOT NULL AND active_scope = 'GLOBAL')",
            name="transport_debug_run_claim_requires_active_scope",
        ),
        CheckConstraint(
            "current_group_index >= 0 AND current_step_ordinal >= 0 AND version > 0",
            name="transport_debug_run_cursor_valid",
        ),
        UniqueConstraint("run_id", name="ux_transport_debug_runs_run_id"),
        UniqueConstraint("active_scope", name="ux_transport_debug_runs_active_scope"),
        Index(
            "ix_transport_debug_runs_claim",
            "claim_until",
            "id",
            postgresql_where=text("active_scope = 'GLOBAL'"),
            sqlite_where=text("active_scope = 'GLOBAL'"),
        ),
        Index("ix_transport_debug_runs_recent", "created_at", "id"),
        {"schema": BIZ_SCHEMA},
    )

    id: int | None = Field(default=None, primary_key=True)
    run_id: str = Field(max_length=80)
    status: str = Field(max_length=30)
    active_scope: str | None = Field(default=None, max_length=20)
    rack_id: str = Field(max_length=100)
    configuration_json: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    current_group_index: int = Field(default=0)
    current_phase: str = Field(max_length=40)
    current_step_ordinal: int = Field(default=0)
    attention_code: str | None = Field(default=None, max_length=120)
    attention_detail: str | None = Field(default=None, sa_type=Text)
    version: int = Field(default=1)
    claim_token: str | None = Field(default=None, max_length=80)
    claim_until: datetime | None = Field(default=None)
    created_by_user_id: int = Field(sa_type=BigInteger)
    aborted_by_user_id: int | None = Field(default=None, sa_type=BigInteger)
    aborted_reason: str | None = Field(default=None, sa_type=Text)
    created_at: datetime
    updated_at: datetime


class TransportDebugRunStep(BaseMixin, table=True):
    """自动联调轮次中每个外部动作或 Evidence 等待步骤。"""

    __tablename__ = "transport_debug_run_steps"  # pyright: ignore[reportAssignmentType]
    __schema__ = BIZ_SCHEMA
    __table_args__ = (
        CheckConstraint(_DEBUG_RUN_STEP_STATUS_CHECK, name="transport_debug_run_step_status_valid"),
        CheckConstraint(_DEBUG_RUN_STEP_PHASE_CHECK, name="transport_debug_run_step_phase_valid"),
        CheckConstraint(
            "ordinal >= 0 AND (group_index IS NULL OR group_index >= 0)",
            name="transport_debug_run_step_cursor_valid",
        ),
        CheckConstraint(
            "evidence_high_watermark IS NULL OR evidence_high_watermark >= 0",
            name="transport_debug_run_step_high_watermark_valid",
        ),
        CheckConstraint(
            "evidence_not_before_ms IS NULL OR evidence_not_before_ms > 0",
            name="transport_debug_run_step_not_before_valid",
        ),
        UniqueConstraint("run_id", "ordinal", name="ux_transport_debug_run_steps_run_ordinal"),
        UniqueConstraint("client_request_id", name="ux_transport_debug_run_steps_client_request_id"),
        Index("ix_transport_debug_run_steps_run_status", "run_id", "status", "ordinal"),
        Index("ix_transport_debug_run_steps_transport_task", "transport_task_id"),
        {"schema": BIZ_SCHEMA},
    )

    id: int | None = Field(default=None, primary_key=True)
    run_id: str = Field(
        foreign_key=f"{BIZ_SCHEMA}.transport_debug_runs.run_id",
        ondelete="CASCADE",
        max_length=80,
    )
    ordinal: int
    group_index: int | None = Field(default=None)
    phase: str = Field(max_length=40)
    status: str = Field(max_length=30)
    client_request_id: str | None = Field(default=None, max_length=120)
    transport_task_id: str | None = Field(default=None, max_length=80)
    evidence_high_watermark: int | None = Field(default=None, sa_type=BigInteger)
    evidence_not_before_ms: int | None = Field(default=None, sa_type=BigInteger)
    observed_bins_json: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False),
    )
    reason_code: str | None = Field(default=None, max_length=120)
    created_at: datetime
    updated_at: datetime


__all__ = ["TransportDebugRun", "TransportDebugRunStep"]
