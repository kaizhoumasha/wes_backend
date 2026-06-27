"""ConveyorQueueMembership (Phase 1 CEO-008, 主计划 §4.4)。

动态队列 active 投影, 以 manifest pipeline_queues.code 作为 queue_code。
替代旧 BinTransitMembership 的中心枚举队列方案。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, BigInteger, Column, Index, text
from sqlmodel import Field

from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation  # noqa: F401
from src.app.runtime.orchestration.execution_session import RUNTIME_SCHEMA
from src.core.mixins.base import BaseMixin


class ConveyorQueueMembership(BaseMixin, table=True):
    """动态输送线队列 membership active/history 投影。"""

    __tablename__ = "conveyor_queue_memberships"
    __schema__ = RUNTIME_SCHEMA
    __table_args__ = (
        Index(
            "ux_wes_runtime_conveyor_queue_memberships_active_bin",
            "workline_id",
            "bin_code",
            unique=True,
            postgresql_where=text("bin_code IS NOT NULL AND membership_status = 'ACTIVE'"),
            sqlite_where=text("bin_code IS NOT NULL AND membership_status = 'ACTIVE'"),
        ),
        Index(
            "ux_wes_runtime_conveyor_queue_memberships_active_placeholder",
            "workline_id",
            "placeholder_key",
            unique=True,
            postgresql_where=text("placeholder_key IS NOT NULL AND membership_status = 'ACTIVE'"),
            sqlite_where=text("placeholder_key IS NOT NULL AND membership_status = 'ACTIVE'"),
        ),
        Index(
            "ix_wes_runtime_conveyor_queue_memberships_workline_queue",
            "workline_id",
            "queue_code",
        ),
        {"schema": RUNTIME_SCHEMA},
    )

    id: int | None = Field(default=None, primary_key=True)

    bin_code: str | None = Field(default=None, max_length=100, index=True)
    placeholder_key: str | None = Field(default=None, max_length=240, index=True)
    workline_id: int = Field(index=True)
    conveyor_code: str = Field(max_length=80, index=True)
    queue_code: str = Field(max_length=80, index=True, description="来自 manifest.pipeline_queues.code")
    queue_role: str = Field(max_length=40, index=True)
    membership_status: str = Field(
        default="ACTIVE",
        max_length=20,
        index=True,
        description="ACTIVE / LEFT / RECONCILING",
    )

    entered_at: int = Field(sa_type=BigInteger, description="Unix timestamp ms")
    left_at: int | None = Field(default=None, sa_type=BigInteger, description="Unix timestamp ms")
    correlation_id: str | None = Field(
        default=None,
        foreign_key=f"{RUNTIME_SCHEMA}.execution_correlations.correlation_id",
        max_length=120,
        index=True,
    )
    evidence_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, default=dict),
        description="active 投影证据",
    )
