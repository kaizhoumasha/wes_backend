"""设备状态观察、回调 evidence 与冲突证据。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from enum import Enum
from typing import Any, ClassVar, cast

from sqlalchemy import JSON, BigInteger, CheckConstraint, Column, Index, UniqueConstraint, text
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.database.schema_conf import SchemaType


class DeviceEvidenceKind(str, Enum):
    RESULT = "RESULT"
    EVENT = "EVENT"


class DeviceEvidenceApplyStatus(str, Enum):
    PENDING = "PENDING"
    APPLIED = "APPLIED"
    IGNORED = "IGNORED"
    RECONCILING = "RECONCILING"


class DeviceStatusObservation(EnterpriseMixin, DataTableMixin, table=True):
    """每次派发准入实际使用的不可变 ECS 状态观察。"""

    __tablename__: ClassVar[str] = "device_status_observations"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index("ix_device_status_observations_device_received", "device_code", "received_at", "id"),
        {"schema": SchemaType.BIZ.value},
    )

    device_code: str = Field(min_length=1, max_length=100)
    command_code: str | None = Field(default=None, max_length=100)
    contract_key: str = Field(min_length=1, max_length=100)
    contract_version: str = Field(min_length=1, max_length=50)
    mode: str = Field(min_length=1, max_length=20)
    status: str = Field(min_length=1, max_length=20)
    current_command_code: str | None = Field(default=None, max_length=100)
    device_timestamp: int = Field(sa_type=BigInteger)
    received_at: datetime
    payload_digest: str = Field(min_length=64, max_length=64)
    raw_payload: dict[str, Any] = Field(sa_column=Column(JSON))


class DeviceEvidence(EnterpriseMixin, DataTableMixin, table=True):
    """已可靠接收、等待异步应用的统一 result/event evidence。"""

    __tablename__: ClassVar[str] = "device_evidences"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        CheckConstraint("kind IN ('RESULT', 'EVENT')", name="device_evidence_kind_valid"),
        CheckConstraint(
            "apply_status IN ('PENDING', 'APPLIED', 'IGNORED', 'RECONCILING')",
            name="device_evidence_apply_status_valid",
        ),
        UniqueConstraint("source_event_id", name="ux_device_evidences_source_event_id"),
        Index(
            "ix_device_evidences_pending",
            "received_at",
            "id",
            postgresql_where=text("apply_status = 'PENDING'"),
            sqlite_where=text("apply_status = 'PENDING'"),
        ),
        Index("ix_device_evidences_command", "command_code", "kind"),
        Index(
            "ux_device_evidences_command_result",
            "command_code",
            unique=True,
            postgresql_where=text("kind = 'RESULT' AND command_code IS NOT NULL"),
            sqlite_where=text("kind = 'RESULT' AND command_code IS NOT NULL"),
        ),
        {"schema": SchemaType.BIZ.value},
    )

    kind: DeviceEvidenceKind = Field(
        sa_type=cast("Any", SQLAEnum(DeviceEvidenceKind, native_enum=False, create_constraint=False, length=10))
    )
    source_event_id: str = Field(min_length=1, max_length=160)
    device_code: str = Field(min_length=1, max_length=100)
    command_code: str | None = Field(default=None, max_length=100)
    contract_key: str = Field(min_length=1, max_length=100)
    contract_version: str = Field(min_length=1, max_length=50)
    line_run_epoch_id: int | None = Field(default=None, foreign_key="wes_biz.line_run_epochs.id", index=True)
    payload_digest: str = Field(min_length=64, max_length=64)
    raw_payload: dict[str, Any] = Field(sa_column=Column(JSON))
    received_at: datetime
    apply_status: DeviceEvidenceApplyStatus = Field(
        default=DeviceEvidenceApplyStatus.PENDING,
        sa_type=cast(
            "Any",
            SQLAEnum(DeviceEvidenceApplyStatus, native_enum=False, create_constraint=False, length=20),
        ),
    )
    processed_at: datetime | None = Field(default=None)
    published_at: datetime | None = Field(default=None)


class DeviceEvidenceConflict(EnterpriseMixin, DataTableMixin, table=True):
    """不推进任何对象的幂等冲突审计证据。"""

    __tablename__: ClassVar[str] = "device_evidence_conflicts"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index("ix_device_evidence_conflicts_source_received", "source_event_id", "received_at", "id"),
        {"schema": SchemaType.BIZ.value},
    )

    source_event_id: str = Field(min_length=1, max_length=160)
    first_evidence_id: int = Field(foreign_key="wes_biz.device_evidences.id", index=True)
    conflicting_digest: str = Field(min_length=64, max_length=64)
    raw_payload: dict[str, Any] = Field(sa_column=Column(JSON))
    reason_code: str = Field(min_length=1, max_length=120)
    received_at: datetime


__all__ = [
    "DeviceEvidence",
    "DeviceEvidenceApplyStatus",
    "DeviceEvidenceConflict",
    "DeviceEvidenceKind",
    "DeviceStatusObservation",
]
