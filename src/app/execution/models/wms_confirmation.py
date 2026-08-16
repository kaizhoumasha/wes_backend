"""WMS 可靠确认义务。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from enum import Enum
from typing import Any, ClassVar, cast

from sqlalchemy import JSON, CheckConstraint, Column, Index, UniqueConstraint, text
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.database.schema_conf import SchemaType


class WmsConfirmationStatus(str, Enum):
    PENDING = "PENDING"
    DISPATCHING = "DISPATCHING"
    COMPLETED = "COMPLETED"
    RECONCILING = "RECONCILING"


class WmsConfirmation(EnterpriseMixin, DataTableMixin, table=True):
    """一次 operation + operation_id 的不可变 WMS 请求与响应证据。"""

    __tablename__: ClassVar[str] = "wms_confirmations"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'DISPATCHING', 'COMPLETED', 'RECONCILING')",
            name="wms_confirmation_status_valid",
        ),
        CheckConstraint("attempt_count >= 0", name="wms_confirmation_attempt_count_nonnegative"),
        UniqueConstraint("operation", "operation_id", name="ux_wms_confirmations_operation_identity"),
        Index(
            "ix_wms_confirmations_dispatch_eligible",
            "status",
            "retry_eligible",
            "next_attempt_at",
            "id",
            postgresql_where=text("status = 'PENDING'"),
            sqlite_where=text("status = 'PENDING'"),
        ),
        {"schema": SchemaType.BIZ.value},
    )

    operation: str = Field(min_length=1, max_length=160)
    operation_id: str = Field(min_length=1, max_length=160)
    material_execution_id: int = Field(foreign_key="wes_biz.material_executions.id", index=True)
    request_digest: str = Field(min_length=64, max_length=64)
    request_payload: dict[str, Any] = Field(sa_column=Column(JSON))
    deadline_at: datetime

    status: WmsConfirmationStatus = Field(
        default=WmsConfirmationStatus.PENDING,
        sa_type=cast(
            "Any",
            SQLAEnum(WmsConfirmationStatus, native_enum=False, create_constraint=False, length=20),
        ),
        index=True,
    )
    attempt_count: int = Field(default=0, ge=0)
    retry_eligible: bool = Field(default=False)
    next_attempt_at: datetime | None = Field(default=None)
    claim_token: str | None = Field(default=None, max_length=80)
    claimed_at: datetime | None = Field(default=None)
    claim_expires_at: datetime | None = Field(default=None)
    last_dispatch_at: datetime | None = Field(default=None)

    response_evidence_id: int | None = Field(
        default=None,
        foreign_key="wes_biz.inbound_evidences.id",
        index=True,
    )
    response_result: str | None = Field(default=None, max_length=80)
    completed_at: datetime | None = Field(default=None)


__all__ = ["WmsConfirmation", "WmsConfirmationStatus"]
