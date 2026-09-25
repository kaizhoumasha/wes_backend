"""一次料箱回程 execution 的当前事实与因果身份。"""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import CheckConstraint, Index, UniqueConstraint, text
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
from src.database.schema_conf import SchemaType


class BinLineReturn(EnterpriseMixin, DataTableMixin, table=True):
    __tablename__: ClassVar[str] = "bin_line_returns"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        UniqueConstraint("scan3_evidence_id", name="ux_bin_line_returns_scan3_evidence"),
        UniqueConstraint("scan4_evidence_id", name="ux_bin_line_returns_scan4_evidence"),
        Index(
            "ux_bin_line_returns_current_bin",
            "workline_id",
            "bin_code",
            unique=True,
            postgresql_where=text("return_state NOT IN ('EXITED', 'VOIDED')"),
            sqlite_where=text("return_state NOT IN ('EXITED', 'VOIDED')"),
        ),
        Index(
            "ix_bin_line_returns_fifo",
            "workline_id",
            "scan4_event_time",
            "scan4_evidence_id",
            postgresql_where=text("scan4_evidence_id IS NOT NULL AND return_state NOT IN ('EXITED', 'VOIDED')"),
            sqlite_where=text("scan4_evidence_id IS NOT NULL AND return_state NOT IN ('EXITED', 'VOIDED')"),
        ),
        CheckConstraint(
            "return_state IN ('NONE', 'MOVE_PENDING', 'READY', 'RETURN_REQUESTED', 'EXITED', 'VOIDED')",
            name="bin_line_returns_state_valid",
        ),
        CheckConstraint(
            "(scan4_evidence_id IS NULL) = (scan4_event_time IS NULL)",
            name="bin_line_returns_scan4_order_complete",
        ),
        {"schema": SchemaType.BIZ.value},
    )

    workline_id: int = Field(foreign_key="wes_biz.work_lines.id", sa_type=SQL_COMPAT_BIGINT)
    bin_code: str = Field(min_length=1, max_length=100)
    scan3_evidence_id: int = Field(foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT)
    scan4_evidence_id: int | None = Field(
        default=None, foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT
    )
    scan4_event_time: int | None = Field(default=None, sa_type=SQL_COMPAT_BIGINT)
    return_batch_evidence_id: int | None = Field(
        default=None, foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT
    )
    scan3_command_code: str | None = Field(default=None, max_length=160)
    scan4_command_code: str | None = Field(default=None, max_length=160)
    return_state: str = Field(default="NONE", max_length=20)


__all__ = ["BinLineReturn"]
