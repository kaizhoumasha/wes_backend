"""人工拣料一次料箱经过的插件私有持久状态。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import ClassVar

from sqlalchemy import CheckConstraint, Index, UniqueConstraint, text
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
from src.database.schema_conf import SchemaType


class ManualPickingPassage(EnterpriseMixin, DataTableMixin, table=True):
    __tablename__: ClassVar[str] = "manual_picking_passages"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        UniqueConstraint("scan1_evidence_id", name="ux_manual_picking_passages_scan1_evidence"),
        UniqueConstraint("scan2_evidence_id", name="ux_manual_picking_passages_scan2_evidence"),
        UniqueConstraint("scan2_fault_evidence_id", name="ux_manual_picking_passages_scan2_fault_evidence"),
        UniqueConstraint("scan3_evidence_id", name="ux_manual_picking_passages_scan3_evidence"),
        UniqueConstraint("scan4_evidence_id", name="ux_manual_picking_passages_scan4_evidence"),
        UniqueConstraint("wms_completed_evidence_id", name="ux_manual_picking_passages_wms_completed_evidence"),
        UniqueConstraint("admission_operation_id", name="ux_manual_picking_passages_admission_operation"),
        Index(
            "ux_manual_picking_passages_wms_terminal",
            "task_id",
            "bin_code",
            unique=True,
            postgresql_where=text("wms_result IS NOT NULL"),
            sqlite_where=text("wms_result IS NOT NULL"),
        ),
        Index(
            "ix_manual_picking_passages_scan2_fifo",
            "workline_id",
            "scan1_received_at",
            "scan1_evidence_id",
            postgresql_where=text("scan2_evidence_id IS NULL AND disposition <> 'CLOSED'"),
            sqlite_where=text("scan2_evidence_id IS NULL AND disposition <> 'CLOSED'"),
        ),
        Index(
            "ix_manual_picking_passages_return_fifo",
            "workline_id",
            "scan4_received_at",
            "scan4_evidence_id",
            postgresql_where=text("scan4_evidence_id IS NOT NULL AND return_state <> 'RETURNED'"),
            sqlite_where=text("scan4_evidence_id IS NOT NULL AND return_state <> 'RETURNED'"),
        ),
        CheckConstraint("disposition IN ('OPEN', 'NORMAL', 'NG', 'CLOSED')", name="manual_picking_disposition_valid"),
        CheckConstraint(
            "return_state IN ('NONE', 'MOVE_PENDING', 'READY', 'RETURN_REQUESTED', 'RETURNED')",
            name="manual_picking_return_state_valid",
        ),
        CheckConstraint("wms_result IS NULL OR wms_result IN ('NORMAL', 'NG')", name="manual_picking_wms_result_valid"),
        CheckConstraint(
            "(scan4_evidence_id IS NULL) = (scan4_received_at IS NULL)",
            name="manual_picking_scan4_order_complete",
        ),
        CheckConstraint(
            "archived_at IS NULL OR disposition = 'CLOSED'",
            name="manual_picking_archive_closed",
        ),
        {"schema": SchemaType.BIZ.value},
    )

    workline_id: int = Field(foreign_key="wes_biz.work_lines.id", sa_type=SQL_COMPAT_BIGINT)
    task_id: str = Field(min_length=1, max_length=100)
    bin_code: str | None = Field(default=None, max_length=100)
    raw_scan1_code: str | None = Field(default=None, max_length=160)
    scan1_evidence_id: int = Field(foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT)
    scan1_received_at: datetime
    scan2_evidence_id: int | None = Field(
        default=None, foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT
    )
    scan2_fault_evidence_id: int | None = Field(
        default=None, foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT
    )
    scan3_evidence_id: int | None = Field(
        default=None, foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT
    )
    scan4_evidence_id: int | None = Field(
        default=None, foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT
    )
    scan4_received_at: datetime | None = Field(default=None)
    disposition: str = Field(default="OPEN", max_length=10)
    admission_operation_id: str | None = Field(default=None, max_length=160)
    admission_result: str | None = Field(default=None, max_length=20)
    admission_scanned_at: int | None = Field(default=None, sa_type=SQL_COMPAT_BIGINT)
    wms_result: str | None = Field(default=None, max_length=10)
    wms_completed_at: datetime | None = Field(default=None)
    wms_completed_evidence_id: int | None = Field(
        default=None, foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT
    )
    scan1_command_code: str | None = Field(default=None, max_length=160)
    scan2_command_code: str | None = Field(default=None, max_length=160)
    scan2_fault_command_code: str | None = Field(default=None, max_length=160)
    scan3_command_code: str | None = Field(default=None, max_length=160)
    scan3_route: str | None = Field(default=None, max_length=20)
    scan4_command_code: str | None = Field(default=None, max_length=160)
    return_state: str = Field(default="NONE", max_length=20)
    archived_at: datetime | None = Field(default=None, description="运维清线归档时间；不代表业务或设备完成")


__all__ = ["ManualPickingPassage"]
