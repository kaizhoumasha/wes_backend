"""首次 SCAN3 后的当前回程根；闭合 Work 仅保留因果标识，不作为恢复依赖。"""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import CheckConstraint, Index, text
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
from src.database.schema_conf import SchemaType


class AutomaticPickingReturn(EnterpriseMixin, DataTableMixin, table=True):
    __tablename__: ClassVar[str] = "automatic_picking_returns"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        CheckConstraint("source_work_id > 0", name="automatic_return_source_work"),
        CheckConstraint(
            "(scan4_evidence_id IS NULL AND scan4_event_time IS NULL) OR "
            "(scan4_evidence_id IS NOT NULL AND scan4_event_time IS NOT NULL AND scan4_event_time > 0)",
            name="automatic_return_scan4_pair",
        ),
        CheckConstraint(
            "ready_evidence_id IS NULL OR (scan4_evidence_id IS NOT NULL AND scan4_command_code IS NOT NULL)",
            name="automatic_return_ready_basis",
        ),
        Index(
            "ix_automatic_return_current_fifo",
            "workline_id",
            "scan4_event_time",
            postgresql_where=text("archived_at IS NULL AND scan4_event_time IS NOT NULL"),
            sqlite_where=text("archived_at IS NULL AND scan4_event_time IS NOT NULL"),
        ),
        {"schema": SchemaType.BIZ.value},
    )
    workline_id: int = Field(foreign_key="wes_biz.work_lines.id", sa_type=SQL_COMPAT_BIGINT)
    bin_code: str = Field(max_length=100)
    source_work_id: int = Field(sa_type=SQL_COMPAT_BIGINT, unique=True)
    entry_command_code: str = Field(foreign_key="wes_biz.device_commands.command_code", max_length=80, unique=True)
    scan3_evidence_id: int = Field(foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT, unique=True)
    scan4_evidence_id: int | None = Field(
        default=None, foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT, unique=True
    )
    scan4_event_time: int | None = Field(default=None, sa_type=SQL_COMPAT_BIGINT)
    scan3_command_code: str | None = Field(
        default=None, foreign_key="wes_biz.device_commands.command_code", max_length=80, unique=True
    )
    scan4_command_code: str | None = Field(
        default=None, foreign_key="wes_biz.device_commands.command_code", max_length=80, unique=True
    )
    ready_evidence_id: int | None = Field(
        default=None, foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT, unique=True
    )
    archived_at: datetime | None = None
