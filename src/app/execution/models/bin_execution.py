"""料箱在一个 WorkLine Epoch 内的最小执行生命周期。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from enum import Enum
from typing import Any, ClassVar, cast

from sqlalchemy import CheckConstraint, Index, UniqueConstraint, text
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.database.schema_conf import SchemaType


class BinExecutionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class BinExecution(EnterpriseMixin, DataTableMixin, table=True):
    """同一料箱同时最多有一个活动执行 owner。"""

    __tablename__: ClassVar[str] = "bin_executions"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE', 'CLOSED')", name="bin_execution_status_valid"),
        UniqueConstraint("execution_code", name="ux_bin_executions_execution_code"),
        Index(
            "ux_bin_executions_active_bin",
            "bin_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
            sqlite_where=text("status = 'ACTIVE'"),
        ),
        Index("ix_bin_executions_epoch_status", "line_run_epoch_id", "status", "id"),
        {"schema": SchemaType.BIZ.value},
    )

    execution_code: str = Field(min_length=1, max_length=120)
    bin_id: str = Field(min_length=1, max_length=100, index=True)
    workline_id: int = Field(foreign_key="wes_biz.work_lines.id", index=True)
    line_run_epoch_id: int = Field(foreign_key="wes_biz.line_run_epochs.id", index=True)
    status: BinExecutionStatus = Field(
        default=BinExecutionStatus.ACTIVE,
        sa_type=cast(
            "Any",
            SQLAEnum(BinExecutionStatus, native_enum=False, create_constraint=False, length=10),
        ),
        index=True,
    )
    started_at: datetime
    closed_at: datetime | None = Field(default=None)


__all__ = ["BinExecution", "BinExecutionStatus"]
