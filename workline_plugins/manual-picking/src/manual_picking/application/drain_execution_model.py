"""插件排空的本地业务生命周期及原返料成员身份。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - SQLModel resolves this annotation at runtime
from typing import ClassVar

from sqlalchemy import CheckConstraint, Index, UniqueConstraint
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
from src.database.schema_conf import SchemaType


class ManualPickingDrainExecution(EnterpriseMixin, DataTableMixin, table=True):
    __tablename__: ClassVar[str] = "manual_picking_drain_executions"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        UniqueConstraint("drain_operation_id", name="ux_manual_picking_drain_execution_operation"),
        Index("ix_manual_picking_drain_execution_workline_status", "workline_id", "status"),
        Index("ix_manual_picking_drain_execution_evidence", "workline_id", "decision_evidence_id"),
        CheckConstraint(
            "(status = 'ACTIVE' AND closed_at IS NULL AND reason_code IS NULL "
            "AND blocker_client_request_id IS NULL AND trigger_picking_task_id IS NULL) OR "
            "(status = 'COMPLETED' AND closed_at IS NOT NULL AND reason_code IS NULL "
            "AND blocker_client_request_id IS NULL AND trigger_picking_task_id IS NULL) OR "
            "(status = 'EXCEPTION_RETIRED' AND closed_at IS NOT NULL "
            "AND reason_code IS NOT NULL AND reason_code IN ('DRAIN_ACTION_TERMINAL_EXCEPTION', 'DRAIN_RESULT_OVERDUE', 'DRAIN_RESULT_UNKNOWN') "
            "AND blocker_client_request_id IS NOT NULL AND trigger_picking_task_id IS NOT NULL) OR "
            "(status = 'MANUAL_WITHDRAWN' AND closed_at IS NOT NULL "
            "AND reason_code IS NOT NULL AND reason_code = 'MANUAL_SCOPE_EXIT' AND blocker_client_request_id IS NOT NULL "
            "AND trigger_picking_task_id IS NOT NULL)",
            name="manual_picking_drain_execution_lifecycle_valid",
        ),
        {"schema": SchemaType.BIZ.value},
    )

    workline_id: int = Field(foreign_key="wes_biz.work_lines.id", sa_type=SQL_COMPAT_BIGINT)
    drain_operation_id: str = Field(min_length=1, max_length=160)
    decision_evidence_id: int = Field(foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT)
    status: str = Field(default="ACTIVE", max_length=20)
    closed_at: datetime | None = None
    reason_code: str | None = Field(default=None, max_length=64)
    blocker_client_request_id: str | None = Field(
        default=None, foreign_key="wes_biz.transport_tasks.client_request_id", max_length=160
    )
    trigger_picking_task_id: int | None = Field(
        default=None, foreign_key="wes_biz.picking_tasks.id", sa_type=SQL_COMPAT_BIGINT
    )


class ManualPickingDrainReturnMember(EnterpriseMixin, DataTableMixin, table=True):
    __tablename__: ClassVar[str] = "manual_picking_drain_return_members"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        UniqueConstraint("return_batch_confirmation_id", "return_id", name="ux_manual_picking_drain_return_member"),
        UniqueConstraint("return_batch_confirmation_id", "sequence_no", name="ux_manual_picking_drain_return_sequence"),
        CheckConstraint("sequence_no > 0", name="manual_picking_drain_return_sequence_positive"),
        Index("ix_manual_picking_drain_return_execution", "execution_id"),
        Index("ix_manual_picking_drain_return_id", "return_id"),
        {"schema": SchemaType.BIZ.value},
    )

    execution_id: int = Field(foreign_key="wes_biz.manual_picking_drain_executions.id", sa_type=SQL_COMPAT_BIGINT)
    return_batch_confirmation_id: int = Field(foreign_key="wes_biz.wms_confirmations.id", sa_type=SQL_COMPAT_BIGINT)
    return_id: int = Field(foreign_key="wes_biz.bin_line_returns.id", sa_type=SQL_COMPAT_BIGINT)
    sequence_no: int


__all__ = ["ManualPickingDrainExecution", "ManualPickingDrainReturnMember"]
