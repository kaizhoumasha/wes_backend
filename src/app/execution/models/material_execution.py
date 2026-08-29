"""单个可执行物料单元的可靠生命周期。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from enum import Enum
from typing import Any, ClassVar, cast

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint, text
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
from src.database.schema_conf import SchemaType


class MaterialExecutionStatus(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    HOLD = "HOLD"
    CLOSED = "CLOSED"
    RECONCILING = "RECONCILING"


class InvalidMaterialExecutionTransitionError(ValueError):
    """请求了未获批的物料执行迁移。"""


_ALLOWED_TRANSITIONS: dict[MaterialExecutionStatus, frozenset[MaterialExecutionStatus]] = {
    MaterialExecutionStatus.CREATED: frozenset(
        {
            MaterialExecutionStatus.RUNNING,
            MaterialExecutionStatus.HOLD,
            MaterialExecutionStatus.RECONCILING,
            MaterialExecutionStatus.CLOSED,
        }
    ),
    MaterialExecutionStatus.RUNNING: frozenset(
        {
            MaterialExecutionStatus.HOLD,
            MaterialExecutionStatus.RECONCILING,
            MaterialExecutionStatus.CLOSED,
        }
    ),
    MaterialExecutionStatus.HOLD: frozenset(
        {
            MaterialExecutionStatus.RUNNING,
            MaterialExecutionStatus.RECONCILING,
            MaterialExecutionStatus.CLOSED,
        }
    ),
    MaterialExecutionStatus.RECONCILING: frozenset(
        {
            MaterialExecutionStatus.RUNNING,
            MaterialExecutionStatus.HOLD,
            MaterialExecutionStatus.CLOSED,
        }
    ),
    MaterialExecutionStatus.CLOSED: frozenset(),
}


class MaterialExecution(EnterpriseMixin, DataTableMixin, table=True):
    """一个 material trace 在一个 Epoch 内的通用执行证据。"""

    __tablename__: ClassVar[str] = "material_executions"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        CheckConstraint(
            "status IN ('CREATED', 'RUNNING', 'HOLD', 'CLOSED', 'RECONCILING')",
            name="material_execution_status_valid",
        ),
        CheckConstraint(
            "status = 'CLOSED' OR (admission_received_at IS NOT NULL AND admission_evidence_id IS NOT NULL)",
            name="material_execution_active_admission_required",
        ),
        ForeignKeyConstraint(
            ["admission_evidence_id"],
            ["wes_biz.inbound_evidences.id"],
            name="fk_material_executions_admission_evidence_id_inbound_evidences",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["last_transition_evidence_id"],
            ["wes_biz.inbound_evidences.id"],
            name="fk_material_executions_last_transition_evidence_id",
            use_alter=True,
        ),
        UniqueConstraint("execution_code", name="ux_material_executions_execution_code"),
        Index(
            "ux_material_executions_active_trace",
            "material_trace_id",
            unique=True,
            postgresql_where=text("status <> 'CLOSED'"),
            sqlite_where=text("status <> 'CLOSED'"),
        ),
        Index("ix_material_executions_epoch_status", "line_run_epoch_id", "status", "id"),
        Index(
            "ix_material_executions_active_fifo",
            "workline_id",
            "line_run_epoch_id",
            "admission_received_at",
            "admission_evidence_id",
            "id",
            postgresql_where=text("status <> 'CLOSED'"),
            sqlite_where=text("status <> 'CLOSED'"),
        ),
        {"schema": SchemaType.BIZ.value},
    )

    execution_code: str = Field(min_length=1, max_length=120)
    material_trace_id: str = Field(min_length=1, max_length=160, index=True)
    workline_id: int = Field(foreign_key="wes_biz.work_lines.id", index=True)
    line_run_epoch_id: int = Field(foreign_key="wes_biz.line_run_epochs.id", index=True)
    admission_received_at: datetime | None = Field(default=None)
    admission_evidence_id: int | None = Field(
        default=None,
        sa_type=SQL_COMPAT_BIGINT,
    )
    status: MaterialExecutionStatus = Field(
        default=MaterialExecutionStatus.CREATED,
        sa_type=cast(
            "Any",
            SQLAEnum(MaterialExecutionStatus, native_enum=False, create_constraint=False, length=20),
        ),
        index=True,
    )
    last_transition_reason: str = Field(min_length=1, max_length=120)
    last_transition_evidence_id: int = Field(
        index=True,
        sa_type=SQL_COMPAT_BIGINT,
    )
    status_changed_at: datetime
    closed_at: datetime | None = Field(default=None)

    def transition_to(
        self,
        target: MaterialExecutionStatus,
        *,
        changed_at: datetime,
        reason_code: str,
        evidence_id: int,
    ) -> None:
        current = MaterialExecutionStatus(self.status)
        if target == current:
            return
        if target not in _ALLOWED_TRANSITIONS[current]:
            raise InvalidMaterialExecutionTransitionError(
                f"不允许 MaterialExecution 从 {current.value} 迁移到 {target.value}"
            )
        self.status = target
        self.status_changed_at = changed_at
        self.last_transition_reason = reason_code
        self.last_transition_evidence_id = evidence_id
        if target is MaterialExecutionStatus.CLOSED:
            self.closed_at = changed_at


__all__ = [
    "InvalidMaterialExecutionTransitionError",
    "MaterialExecution",
    "MaterialExecutionStatus",
]
