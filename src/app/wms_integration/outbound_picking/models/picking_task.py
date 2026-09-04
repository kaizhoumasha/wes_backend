"""WMS 发布的统一 PickingTask 队列记录。"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, ClassVar, cast

from sqlalchemy import BigInteger, CheckConstraint, Index, UniqueConstraint, text
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
from src.database.schema_conf import SchemaType


class PickingTaskStatus(StrEnum):
    QUEUED = "QUEUED"
    PREPARING = "PREPARING"
    EXECUTING = "EXECUTING"
    EXECUTION_COMPLETED = "EXECUTION_COMPLETED"


class PickingTaskType(StrEnum):
    MANUAL = "MANUAL"
    AUTO = "AUTO"


class PickingTask(EnterpriseMixin, DataTableMixin, table=True):
    """一次 WMS PickingTask 在 WES 中的本地可靠身份。"""

    __tablename__: ClassVar[str] = "picking_tasks"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        CheckConstraint(
            "status IN ('QUEUED', 'PREPARING', 'EXECUTING', 'EXECUTION_COMPLETED')",
            name="picking_task_status_valid",
        ),
        CheckConstraint("task_type IN ('MANUAL', 'AUTO')", name="picking_task_type_valid"),
        CheckConstraint("queue_revision >= 1", name="picking_task_queue_revision_positive"),
        CheckConstraint("dispatch_sequence >= 1", name="picking_task_dispatch_sequence_positive"),
        CheckConstraint("issued_at_ms > 0", name="picking_task_issued_at_positive"),
        CheckConstraint(
            "not_before_ms IS NULL OR not_before_ms >= 0",
            name="picking_task_not_before_nonnegative",
        ),
        UniqueConstraint("task_id", name="ux_picking_tasks_task_id"),
        UniqueConstraint("issued_evidence_id", name="ux_picking_tasks_issued_evidence"),
        Index(
            "ux_picking_tasks_queued_dispatch_sequence",
            "dispatch_sequence",
            unique=True,
            postgresql_where=text("status = 'QUEUED'"),
            sqlite_where=text("status = 'QUEUED'"),
        ),
        Index("ix_picking_tasks_queue", "status", "not_before_ms", "dispatch_sequence", "id"),
        {"schema": SchemaType.BIZ.value},
    )

    task_id: str = Field(min_length=1, max_length=100)
    task_type: PickingTaskType = Field(
        sa_type=cast(
            "Any",
            SQLAEnum(PickingTaskType, native_enum=False, create_constraint=False, length=6),
        ),
    )
    status: PickingTaskStatus = Field(
        default=PickingTaskStatus.QUEUED,
        sa_type=cast(
            "Any",
            SQLAEnum(PickingTaskStatus, native_enum=False, create_constraint=False, length=24),
        ),
    )
    queue_revision: int = Field(ge=1, sa_type=BigInteger)
    dispatch_sequence: int = Field(ge=1, sa_type=BigInteger)
    not_before_ms: int | None = Field(default=None, ge=0, sa_type=BigInteger)
    issued_at_ms: int = Field(gt=0, sa_type=BigInteger)
    issued_evidence_id: int = Field(
        foreign_key="wes_biz.inbound_evidences.id",
        sa_type=SQL_COMPAT_BIGINT,
    )


__all__ = ["PickingTask", "PickingTaskStatus", "PickingTaskType"]
