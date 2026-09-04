"""设备与 WMS 共用的规范化入站证据。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from enum import Enum
from typing import Any, ClassVar, cast

from sqlalchemy import JSON, CheckConstraint, Column, Index, UniqueConstraint, text
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
from src.database.schema_conf import SchemaType


class InboundEvidenceKind(str, Enum):
    DEVICE_EVENT = "DEVICE_EVENT"
    DEVICE_RESULT = "DEVICE_RESULT"
    TRANSPORT_RESULT = "TRANSPORT_RESULT"
    WMS_EVENT = "WMS_EVENT"
    WMS_RESULT = "WMS_RESULT"


class InboundEvidenceApplyStatus(str, Enum):
    PENDING = "PENDING"
    APPLIED = "APPLIED"
    IGNORED = "IGNORED"
    RECONCILING = "RECONCILING"


class InboundEvidence(EnterpriseMixin, DataTableMixin, table=True):
    """先于 ACK 持久化的不可变规范化 evidence。"""

    __tablename__: ClassVar[str] = "inbound_evidences"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        CheckConstraint(
            "kind IN ('DEVICE_EVENT', 'DEVICE_RESULT', 'TRANSPORT_RESULT', 'WMS_EVENT', 'WMS_RESULT')",
            name="inbound_evidence_kind_valid",
        ),
        CheckConstraint(
            "apply_status IN ('PENDING', 'APPLIED', 'IGNORED', 'RECONCILING')",
            name="inbound_evidence_apply_status_valid",
        ),
        CheckConstraint(
            "kind NOT IN ('WMS_EVENT', 'WMS_RESULT') OR (operation IS NOT NULL AND operation_id IS NOT NULL)",
            name="inbound_evidence_wms_identity_required",
        ),
        CheckConstraint(
            "kind NOT IN ('DEVICE_EVENT', 'DEVICE_RESULT') OR device_code IS NOT NULL",
            name="inbound_evidence_device_identity_required",
        ),
        CheckConstraint(
            "(kind = 'TRANSPORT_RESULT') = (transport_task_id IS NOT NULL)",
            name="inbound_evidence_transport_identity_required",
        ),
        CheckConstraint(
            "kind <> 'TRANSPORT_RESULT' OR "
            "(device_code IS NULL AND command_code IS NULL AND operation IS NULL AND operation_id IS NULL)",
            name="inbound_evidence_transport_identity_isolated",
        ),
        CheckConstraint("decision_attempt_count >= 0", name="inbound_evidence_decision_attempt_count_nonnegative"),
        CheckConstraint(
            "(decision_claim_token IS NULL) = (decision_claim_expires_at IS NULL)",
            name="inbound_evidence_decision_claim_complete",
        ),
        CheckConstraint(
            "published_at IS NULL OR (decision_digest IS NOT NULL AND decision_claim_token IS NULL)",
            name="inbound_evidence_published_decision_complete",
        ),
        UniqueConstraint("source_identity", name="ux_inbound_evidences_source_identity"),
        Index(
            "ix_inbound_evidences_pending",
            "received_at",
            "id",
            postgresql_where=text("apply_status = 'PENDING'"),
            sqlite_where=text("apply_status = 'PENDING'"),
        ),
        Index(
            "ix_inbound_evidences_decision_eligible",
            "decision_next_attempt_at",
            "decision_claim_expires_at",
            "received_at",
            "id",
            postgresql_where=text(
                "apply_status = 'APPLIED' AND published_at IS NULL "
                "AND NOT (kind = 'DEVICE_RESULT' AND material_execution_id IS NULL)"
            ),
            sqlite_where=text(
                "apply_status = 'APPLIED' AND published_at IS NULL "
                "AND NOT (kind = 'DEVICE_RESULT' AND material_execution_id IS NULL)"
            ),
        ),
        Index("ix_inbound_evidences_device_command", "device_code", "command_code", "kind"),
        Index("ix_inbound_evidences_transport_task", "transport_task_id", "kind"),
        Index(
            "ix_inbound_evidences_device_event_range",
            "received_at",
            "id",
            postgresql_where=text("kind = 'DEVICE_EVENT'"),
            sqlite_where=text("kind = 'DEVICE_EVENT'"),
        ),
        Index(
            "ux_inbound_evidences_device_result",
            "command_code",
            unique=True,
            postgresql_where=text("kind = 'DEVICE_RESULT' AND command_code IS NOT NULL"),
            sqlite_where=text("kind = 'DEVICE_RESULT' AND command_code IS NOT NULL"),
        ),
        Index(
            "ux_inbound_evidences_wms_identity",
            "operation",
            "operation_id",
            unique=True,
            postgresql_where=text("kind IN ('WMS_EVENT', 'WMS_RESULT')"),
            sqlite_where=text("kind IN ('WMS_EVENT', 'WMS_RESULT')"),
        ),
        {"schema": SchemaType.BIZ.value},
    )

    kind: InboundEvidenceKind = Field(
        sa_type=cast("Any", SQLAEnum(InboundEvidenceKind, native_enum=False, create_constraint=False, length=20))
    )
    source_identity: str = Field(min_length=1, max_length=300)
    payload_digest: str = Field(min_length=64, max_length=64)
    normalized_payload: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    received_at: datetime

    line_run_epoch_id: int | None = Field(default=None, foreign_key="wes_biz.line_run_epochs.id", index=True)
    material_execution_id: int | None = Field(default=None, foreign_key="wes_biz.material_executions.id", index=True)
    transport_task_id: str | None = Field(default=None, max_length=120, index=True)
    device_code: str | None = Field(default=None, max_length=100, index=True)
    command_code: str | None = Field(default=None, max_length=100, index=True)
    contract_key: str | None = Field(default=None, max_length=100)
    contract_version: str | None = Field(default=None, max_length=50)
    operation: str | None = Field(default=None, max_length=160, index=True)
    operation_id: str | None = Field(default=None, max_length=160, index=True)

    apply_status: InboundEvidenceApplyStatus = Field(
        default=InboundEvidenceApplyStatus.PENDING,
        sa_type=cast(
            "Any",
            SQLAEnum(InboundEvidenceApplyStatus, native_enum=False, create_constraint=False, length=20),
        ),
        index=True,
    )
    processed_at: datetime | None = Field(default=None)
    published_at: datetime | None = Field(default=None)
    decision_digest: str | None = Field(default=None, min_length=64, max_length=64)
    decision_attempt_count: int = Field(default=0, ge=0)
    decision_next_attempt_at: datetime | None = Field(default=None)
    decision_claim_token: str | None = Field(default=None, max_length=80)
    decision_claim_expires_at: datetime | None = Field(default=None)


class InboundEvidenceConflict(EnterpriseMixin, DataTableMixin, table=True):
    """同一稳定身份不同语义载荷的对账证据。"""

    __tablename__: ClassVar[str] = "inbound_evidence_conflicts"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index("ix_inbound_evidence_conflicts_source_received", "source_identity", "received_at", "id"),
        {"schema": SchemaType.BIZ.value},
    )

    source_identity: str = Field(min_length=1, max_length=300)
    first_evidence_id: int = Field(
        foreign_key="wes_biz.inbound_evidences.id",
        index=True,
        sa_type=SQL_COMPAT_BIGINT,
    )
    conflicting_digest: str = Field(min_length=64, max_length=64)
    normalized_payload: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    reason_code: str = Field(min_length=1, max_length=120)
    received_at: datetime


__all__ = [
    "InboundEvidence",
    "InboundEvidenceApplyStatus",
    "InboundEvidenceConflict",
    "InboundEvidenceKind",
]
