"""AGV/CTU 搬运任务、成员、证据和位置投影模型。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - SQLModel resolves this annotation at runtime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, Index, UniqueConstraint, text
from sqlmodel import Field

from src.app.transport.contracts import MAX_SUBMIT_ATTEMPTS
from src.core.mixins.base import BaseMixin
from src.database.schema_conf import SchemaType

RUNTIME_SCHEMA = SchemaType.RUNTIME.value

# 六态只描述可靠搬运事实；RECONCILING 可被晚到的权威结果修正为确定终态。
_TASK_STATUS_CHECK = "status IN ('PENDING', 'ACCEPTED', 'REJECTED', 'SUCCEEDED', 'FAILED', 'RECONCILING')"
_EVIDENCE_STATUS_CHECK = "status IN ('PENDING', 'APPLIED', 'CONFLICT')"


class TransportTask(BaseMixin, table=True):
    __tablename__ = "transport_tasks"  # pyright: ignore[reportAssignmentType]
    __schema__ = RUNTIME_SCHEMA
    __table_args__ = (
        CheckConstraint(_TASK_STATUS_CHECK, name="transport_task_status_valid"),
        CheckConstraint(
            f"submit_attempt_count BETWEEN 0 AND {MAX_SUBMIT_ATTEMPTS}",
            name="transport_submit_attempt_count_valid",
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
    payload_digest: str = Field(max_length=64)
    kind: str = Field(max_length=30)
    caller_json: dict[str, Any] = Field(sa_type=JSON)
    request_json: dict[str, Any] = Field(sa_type=JSON)
    status: str = Field(default="PENDING", max_length=20)
    reason_code: str | None = Field(default=None, max_length=120)

    submit_attempt_count: int = Field(default=0)
    next_submit_at: datetime | None = Field(default=None)
    send_started_at: datetime | None = Field(default=None)
    result_deadline_at: datetime | None = Field(default=None)
    submit_claim_token: str | None = Field(default=None, max_length=80)
    submit_claim_until: datetime | None = Field(default=None)

    outcome_version: int = Field(default=0)
    published_outcome_version: int = Field(default=0)
    outcome_json: dict[str, Any] | None = Field(default=None, sa_type=JSON)
    outcome_claim_token: str | None = Field(default=None, max_length=80)
    outcome_claim_until: datetime | None = Field(default=None)

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
    arrival_face: str | None = Field(default=None, max_length=1)
    last_event_id: str | None = Field(default=None, max_length=120)
    updated_at: datetime


class TransportEvidence(BaseMixin, table=True):
    __tablename__ = "transport_evidence"  # pyright: ignore[reportAssignmentType]
    __schema__ = RUNTIME_SCHEMA
    __table_args__ = (
        CheckConstraint(_EVIDENCE_STATUS_CHECK, name="transport_evidence_status_valid"),
        UniqueConstraint("operation", "event_id", name="ux_transport_evidence_operation_event_id"),
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
    event_id: str = Field(max_length=120)
    transport_task_id: str = Field(max_length=80, index=True)
    operation: str = Field(max_length=80)
    payload_digest: str = Field(max_length=64)
    payload_json: dict[str, Any] = Field(sa_type=JSON)
    status: str = Field(default="PENDING", max_length=20)
    claim_token: str | None = Field(default=None, max_length=80)
    claim_until: datetime | None = Field(default=None)
    received_at: datetime
    processed_at: datetime | None = Field(default=None)
    conflict_code: str | None = Field(default=None, max_length=120)


class TransportPositionProjection(BaseMixin, table=True):
    __tablename__ = "transport_position_projections"  # pyright: ignore[reportAssignmentType]
    __schema__ = RUNTIME_SCHEMA
    __table_args__ = (
        UniqueConstraint("object_type", "object_id", name="ux_transport_position_projection_object"),
        {"schema": RUNTIME_SCHEMA},
    )

    id: int | None = Field(default=None, primary_key=True)
    object_type: str = Field(max_length=10)
    object_id: str = Field(max_length=100)
    position_json: dict[str, Any] | None = Field(default=None, sa_type=JSON)
    position_unknown: bool = Field(default=False)
    arrival_face: str | None = Field(default=None, max_length=1)
    source_event_id: str = Field(max_length=120)
    updated_at: datetime


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
    "TransportEvidence",
    "TransportMember",
    "TransportPositionProjection",
    "TransportResourceBinding",
    "TransportTask",
]
