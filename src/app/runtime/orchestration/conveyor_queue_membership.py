"""ConveyorQueueMembership (主计划 §4.4)。

动态队列 active 投影, 以 manifest pipeline_queues.code 作为 queue_code。
替代旧中心枚举队列方案。
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - SQLModel 运行时需要解析字段类型
from typing import Any

from sqlalchemy import JSON, BigInteger, CheckConstraint, Column, ForeignKeyConstraint, Index, text
from sqlmodel import Field

from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation  # noqa: F401
from src.app.runtime.orchestration.execution_session import RUNTIME_SCHEMA
from src.core.mixins.base import BaseMixin


class ConveyorQueueMembership(BaseMixin, table=True):
    """动态输送线队列 membership active/history 投影。"""

    __tablename__ = "conveyor_queue_memberships"  # pyright: ignore[reportAssignmentType]
    __schema__ = RUNTIME_SCHEMA
    __table_args__ = (
        ForeignKeyConstraint(
            ["route_instance_id"],
            [f"{RUNTIME_SCHEMA}.bin_route_instances.route_instance_id"],
            name="fk_conveyor_queue_memberships_route_instance",
        ),
        ForeignKeyConstraint(
            ["e13_claim_intent_id"],
            [f"{RUNTIME_SCHEMA}.runtime_intent_logs.id"],
            name="fk_conveyor_queue_memberships_e13_claim_intent",
        ),
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
        Index(
            "ux_wes_runtime_conveyor_queue_memberships_active_route",
            "route_instance_id",
            unique=True,
            postgresql_where=text("route_instance_id IS NOT NULL AND membership_status IN ('ACTIVE', 'RECONCILING')"),
            sqlite_where=text("route_instance_id IS NOT NULL AND membership_status IN ('ACTIVE', 'RECONCILING')"),
        ),
        Index(
            "ix_wes_runtime_conveyor_queue_memberships_return_fifo_unclaimed",
            "workline_id",
            "queue_code",
            "scan3_enqueued_at",
            "queue_position",
            "bin_code",
            postgresql_where=text(
                "membership_status = 'ACTIVE' AND queue_role = 'RETURN_QUEUE' AND e13_claim_intent_id IS NULL"
            ),
            sqlite_where=text(
                "membership_status = 'ACTIVE' AND queue_role = 'RETURN_QUEUE' AND e13_claim_intent_id IS NULL"
            ),
        ),
        Index(
            "ux_wes_runtime_conveyor_queue_memberships_active_entry_position",
            "workline_id",
            "queue_code",
            "queue_position",
            unique=True,
            postgresql_where=text("membership_status IN ('ACTIVE', 'RECONCILING') AND queue_role = 'ENTRY'"),
            sqlite_where=text("membership_status IN ('ACTIVE', 'RECONCILING') AND queue_role = 'ENTRY'"),
        ),
        # DB 端 membership_status 强约束: 防止 case/whitespace 漂移导致
        # partial unique index 的 postgresql_where='membership_status = ''ACTIVE'''
        # 漏匹配, 破坏 ACTIVE 唯一性。
        CheckConstraint(
            "membership_status IN ('ACTIVE', 'LEFT', 'RECONCILING')",
            name="ck_wes_runtime_conveyor_queue_memberships_status",
        ),
        CheckConstraint(
            "NOT (membership_status IN ('ACTIVE', 'RECONCILING') "
            "AND queue_role = 'RETURN_QUEUE') OR ("
            "route_instance_id IS NOT NULL "
            "AND scan3_enqueued_at IS NOT NULL "
            "AND queue_position IS NOT NULL "
            "AND queue_position > 0 "
            "AND bin_code IS NOT NULL"
            ")",
            name="return_shape",
        ),
        CheckConstraint(
            "NOT (membership_status IN ('ACTIVE', 'RECONCILING') "
            "AND queue_role = 'ENTRY') OR ("
            "route_instance_id IS NOT NULL "
            "AND queue_position IS NOT NULL "
            "AND queue_position > 0 "
            "AND bin_code IS NOT NULL"
            ")",
            name="entry_shape",
        ),
        CheckConstraint(
            "("
            "e13_claim_intent_id IS NULL "
            "AND e13_claim_token IS NULL "
            "AND e13_claim_until IS NULL"
            ") OR ("
            "e13_claim_intent_id IS NOT NULL "
            "AND e13_claim_token IS NOT NULL "
            "AND e13_claim_until IS NOT NULL "
            "AND membership_status IN ('ACTIVE', 'RECONCILING') "
            "AND queue_role = 'RETURN_QUEUE'"
            ")",
            name="claim_shape",
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
        description="ACTIVE / LEFT / RECONCILING (DB CheckConstraint 强约束)",
    )

    entered_at: int = Field(sa_type=BigInteger, description="Unix timestamp ms")
    left_at: int | None = Field(default=None, sa_type=BigInteger, description="Unix timestamp ms")
    route_instance_id: str | None = Field(default=None, max_length=160, index=True)
    scan3_enqueued_at: datetime | None = Field(default=None)
    queue_position: int | None = Field(default=None, ge=1)
    e13_claim_intent_id: int | None = Field(default=None, index=True)
    e13_claim_token: str | None = Field(default=None, max_length=64)
    e13_claim_until: datetime | None = Field(default=None, index=True)
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
