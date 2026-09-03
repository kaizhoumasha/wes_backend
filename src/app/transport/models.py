"""AGV/CTU 搬运任务、成员、证据和位置投影模型。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - SQLModel resolves this annotation at runtime
from typing import Any

from sqlalchemy import JSON, BigInteger, CheckConstraint, Column, Index, Text, UniqueConstraint, text
from sqlmodel import Field

from src.app.transport.contracts import MAX_SUBMIT_ATTEMPTS
from src.core.mixins.base import BaseMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
from src.database.schema_conf import SchemaType

RUNTIME_SCHEMA = SchemaType.RUNTIME.value

# 六态只描述可靠搬运事实：PENDING 经权威 ACK 进入 ACCEPTED，经结果 evidence 进入确定终态；
# DELIVERY_UNKNOWN/冲突/超时进入 RECONCILING，匹配不可变身份的迟到权威事实仍可单调收敛。
_TASK_STATUS_CHECK = "status IN ('PENDING', 'ACCEPTED', 'REJECTED', 'SUCCEEDED', 'FAILED', 'RECONCILING')"
_EVIDENCE_STATUS_CHECK = "status IN ('PENDING', 'APPLIED', 'CONFLICT')"
_EVIDENCE_REVISION_OPERATION_CHECK = (
    "(operation = 'transport.task.resulted@v1' AND outcome_revision IS NOT NULL) OR "
    "(operation <> 'transport.task.resulted@v1' AND outcome_revision IS NULL)"
)
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


class TransportTask(BaseMixin, table=True):
    __tablename__ = "transport_tasks"  # pyright: ignore[reportAssignmentType]
    __schema__ = RUNTIME_SCHEMA
    __table_args__ = (
        CheckConstraint(_TASK_STATUS_CHECK, name="transport_task_status_valid"),
        CheckConstraint(
            "(authority_workline_id IS NULL AND authority_line_run_epoch_id IS NULL "
            "AND authority_bin_execution_id IS NULL) OR "
            "(authority_workline_id IS NOT NULL AND authority_line_run_epoch_id IS NOT NULL)",
            name="transport_execution_authority_all_or_none",
        ),
        CheckConstraint(
            f"submit_attempt_count BETWEEN 0 AND {MAX_SUBMIT_ATTEMPTS}",
            name="transport_submit_attempt_count_valid",
        ),
        CheckConstraint(
            "last_applied_wms_outcome_revision >= 0",
            name="transport_last_applied_wms_outcome_revision_valid",
        ),
        UniqueConstraint("transport_task_id", name="ux_transport_tasks_task_id"),
        UniqueConstraint("client_request_id", name="ux_transport_tasks_client_request_id"),
        Index(
            "ix_transport_tasks_submit_claim",
            text("(next_submit_at IS NOT NULL) ASC"),
            "next_submit_at",
            "id",
            postgresql_where=text("status = 'PENDING'"),
            sqlite_where=text("status = 'PENDING'"),
        ),
        Index(
            "ix_transport_tasks_result_deadline",
            "result_deadline_at",
            "id",
            postgresql_where=text("status = 'ACCEPTED' AND result_deadline_at IS NOT NULL"),
            sqlite_where=text("status = 'ACCEPTED' AND result_deadline_at IS NOT NULL"),
        ),
        Index(
            "ix_transport_tasks_ambiguous_claim",
            "submit_claim_until",
            "id",
            postgresql_where=text("status = 'PENDING' AND send_started_at IS NOT NULL"),
            sqlite_where=text("status = 'PENDING' AND send_started_at IS NOT NULL"),
        ),
        Index(
            "ix_transport_tasks_outcome_claim",
            "updated_at",
            "id",
            postgresql_where=text("outcome_version > published_outcome_version"),
            sqlite_where=text("outcome_version > published_outcome_version"),
        ),
        {"schema": RUNTIME_SCHEMA},
    )

    id: int | None = Field(default=None, primary_key=True)
    transport_task_id: str = Field(max_length=80)
    client_request_id: str = Field(max_length=120)
    request_digest: str = Field(max_length=64)
    kind: str = Field(max_length=30)
    caller_json: dict[str, Any] = Field(sa_type=JSON)
    request_json: dict[str, Any] = Field(sa_type=JSON)
    submit_operation_id: str = Field(max_length=36)
    submit_timestamp_ms: int = Field(sa_type=BigInteger)
    submit_request_body: str = Field(sa_type=Text)
    submit_request_body_digest: str = Field(max_length=64)
    status: str = Field(default="PENDING", max_length=20)
    reason_code: str | None = Field(default=None, max_length=120)
    authority_workline_id: int | None = Field(default=None, foreign_key="wes_biz.work_lines.id")
    authority_line_run_epoch_id: int | None = Field(default=None, foreign_key="wes_biz.line_run_epochs.id")
    authority_bin_execution_id: int | None = Field(
        default=None,
        foreign_key="wes_biz.bin_executions.id",
        sa_type=SQL_COMPAT_BIGINT,
    )

    submit_attempt_count: int = Field(default=0)
    next_submit_at: datetime | None = Field(default=None)
    send_started_at: datetime | None = Field(default=None)
    result_deadline_at: datetime | None = Field(default=None)
    submit_claim_token: str | None = Field(default=None, max_length=80)
    submit_claim_until: datetime | None = Field(default=None)

    outcome_version: int = Field(default=0)
    published_outcome_version: int = Field(default=0)
    last_applied_wms_outcome_revision: int = Field(default=0, sa_type=BigInteger)
    outcome_json: dict[str, Any] | None = Field(default=None, sa_type=JSON)
    outcome_claim_token: str | None = Field(default=None, max_length=80)
    outcome_claim_until: datetime | None = Field(default=None)

    created_at: datetime
    updated_at: datetime


class TransportDebugRun(BaseMixin, table=True):
    """自动联调轮次的冻结配置与可恢复游标。"""

    __tablename__ = "transport_debug_runs"  # pyright: ignore[reportAssignmentType]
    __schema__ = RUNTIME_SCHEMA
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
        {"schema": RUNTIME_SCHEMA},
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
    __schema__ = RUNTIME_SCHEMA
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
        {"schema": RUNTIME_SCHEMA},
    )

    id: int | None = Field(default=None, primary_key=True)
    run_id: str = Field(
        foreign_key=f"{RUNTIME_SCHEMA}.transport_debug_runs.run_id",
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


class TransportMember(BaseMixin, table=True):
    __tablename__ = "transport_members"  # pyright: ignore[reportAssignmentType]
    __schema__ = RUNTIME_SCHEMA
    __table_args__ = (
        UniqueConstraint("transport_task_id", "ordinal", name="ux_transport_members_task_ordinal"),
        UniqueConstraint("transport_task_id", "object_id", name="ux_transport_members_task_object"),
        {"schema": RUNTIME_SCHEMA},
    )

    id: int | None = Field(default=None, primary_key=True)
    transport_task_id: str = Field(
        foreign_key=f"{RUNTIME_SCHEMA}.transport_tasks.transport_task_id",
        max_length=80,
        index=True,
    )
    ordinal: int
    object_type: str = Field(max_length=10)
    object_id: str = Field(max_length=100)
    source_json: dict[str, Any] = Field(sa_type=JSON)
    target_json: dict[str, Any] = Field(sa_type=JSON)
    status: str = Field(default="PENDING", max_length=20)
    final_position_json: dict[str, Any] | None = Field(default=None, sa_type=JSON)
    position_unknown: bool = Field(default=False)
    failure_code: str | None = Field(default=None, max_length=120)
    arrival_face: str | None = Field(default=None, sa_type=Text)
    last_operation_id: str | None = Field(default=None, max_length=36)
    updated_at: datetime


class TransportDebugPositionProjection(BaseMixin, table=True):
    """由已应用 Transport 联调终态维护的可丢弃当前位置投影。"""

    __tablename__ = "transport_debug_position_projections"  # pyright: ignore[reportAssignmentType]
    __schema__ = RUNTIME_SCHEMA
    __table_args__ = (
        CheckConstraint(
            "object_type IN ('RACK', 'BIN')",
            name="transport_debug_position_projection_object_type_valid",
        ),
        UniqueConstraint(
            "object_type",
            "object_id",
            name="ux_transport_debug_position_projection_object",
        ),
        Index(
            "ix_transport_debug_position_projection_source_task",
            "source_transport_task_id",
        ),
        {"schema": RUNTIME_SCHEMA},
    )

    id: int | None = Field(default=None, primary_key=True)
    object_type: str = Field(max_length=10)
    object_id: str = Field(max_length=100)
    position_json: dict[str, Any] | None = Field(default=None, sa_type=JSON)
    position_unknown: bool = Field(default=False)
    arrival_face: str | None = Field(default=None, sa_type=Text)
    source_operation_id: str = Field(max_length=36)
    source_transport_task_id: str = Field(
        foreign_key=f"{RUNTIME_SCHEMA}.transport_tasks.transport_task_id",
        ondelete="CASCADE",
        max_length=80,
    )
    updated_at: datetime


class TransportEvidence(BaseMixin, table=True):
    __tablename__ = "transport_evidence"  # pyright: ignore[reportAssignmentType]
    __schema__ = RUNTIME_SCHEMA
    __table_args__ = (
        CheckConstraint(_EVIDENCE_STATUS_CHECK, name="transport_evidence_status_valid"),
        CheckConstraint(
            "outcome_revision IS NULL OR outcome_revision > 0",
            name="transport_evidence_outcome_revision_valid",
        ),
        CheckConstraint(
            _EVIDENCE_REVISION_OPERATION_CHECK,
            name="transport_evidence_outcome_revision_operation_valid",
        ),
        UniqueConstraint("operation", "operation_id", name="ux_transport_evidence_operation_operation_id"),
        UniqueConstraint(
            "transport_task_id",
            "outcome_revision",
            name="ux_transport_evidence_task_outcome_revision",
        ),
        Index(
            "ix_transport_evidence_pending_claim",
            "status",
            "received_at",
            "id",
            postgresql_where=text("status = 'PENDING'"),
            sqlite_where=text("status = 'PENDING'"),
        ),
        {"schema": RUNTIME_SCHEMA},
    )

    id: int | None = Field(default=None, primary_key=True)
    operation_id: str = Field(max_length=36)
    transport_task_id: str = Field(max_length=80, index=True)
    operation: str = Field(max_length=80)
    outcome_revision: int | None = Field(default=None, sa_type=BigInteger)
    event_timestamp_ms: int = Field(sa_type=BigInteger)
    message_digest: str = Field(max_length=64)
    payload_json: dict[str, Any] = Field(sa_type=JSON)
    ack_timestamp_ms: int = Field(sa_type=BigInteger)
    ack_data_json: dict[str, Any] = Field(sa_type=JSON)
    status: str = Field(default="PENDING", max_length=20)
    claim_token: str | None = Field(default=None, max_length=80)
    claim_until: datetime | None = Field(default=None)
    received_at: datetime
    processed_at: datetime | None = Field(default=None)
    conflict_code: str | None = Field(default=None, max_length=120)


class TransportCallbackReceipt(BaseMixin, table=True):
    __tablename__ = "transport_callback_receipts"  # pyright: ignore[reportAssignmentType]
    __schema__ = RUNTIME_SCHEMA
    __table_args__ = (
        CheckConstraint(
            "(conflict_code IS NULL) = (conflict_detected_at IS NULL)",
            name="transport_callback_receipt_conflict_complete",
        ),
        UniqueConstraint("operation", "operation_id", name="ux_transport_callback_receipts_identity"),
        {"schema": RUNTIME_SCHEMA},
    )

    id: int | None = Field(default=None, primary_key=True)
    operation_id: str = Field(max_length=36)
    operation: str = Field(max_length=80)
    message_digest: str = Field(max_length=64)
    message_json: dict[str, Any] = Field(sa_type=JSON)
    response_http_status: int
    response_code: str = Field(max_length=20)
    response_timestamp_ms: int = Field(sa_type=BigInteger)
    response_data_json: dict[str, Any] = Field(sa_type=JSON)
    received_at: datetime
    conflict_code: str | None = Field(default=None, max_length=120)
    conflict_detected_at: datetime | None = Field(default=None)


class TransportResourceBinding(BaseMixin, table=True):
    __tablename__ = "transport_resource_bindings"  # pyright: ignore[reportAssignmentType]
    __schema__ = RUNTIME_SCHEMA
    __table_args__ = (
        Index(
            "ux_transport_resource_bindings_active",
            "resource_type",
            "resource_id",
            unique=True,
            postgresql_where=text("released_at IS NULL"),
            sqlite_where=text("released_at IS NULL"),
        ),
        Index("ix_transport_resource_bindings_task", "transport_task_id", "released_at"),
        {"schema": RUNTIME_SCHEMA},
    )

    id: int | None = Field(default=None, primary_key=True)
    transport_task_id: str = Field(
        foreign_key=f"{RUNTIME_SCHEMA}.transport_tasks.transport_task_id",
        max_length=80,
    )
    resource_type: str = Field(max_length=10)
    resource_id: str = Field(max_length=100)
    created_at: datetime
    released_at: datetime | None = Field(default=None)


__all__ = [
    "TransportCallbackReceipt",
    "TransportDebugPositionProjection",
    "TransportDebugRun",
    "TransportDebugRunStep",
    "TransportEvidence",
    "TransportMember",
    "TransportResourceBinding",
    "TransportTask",
]
