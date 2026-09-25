"""当前 inbound Batch 中已经进入执行的箱。"""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import UniqueConstraint
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
from src.database.schema_conf import SchemaType


class ManualPickingInboundBatch(EnterpriseMixin, DataTableMixin, table=True):
    __tablename__: ClassVar[str] = "manual_picking_inbound_batches"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        UniqueConstraint("operation_id", name="ux_manual_picking_inbound_batches_operation"),
        UniqueConstraint(
            "workline_id",
            "task_id",
            "plan_revision",
            "rack_id",
            "rack_face",
            name="ux_manual_picking_inbound_batches_face",
        ),
        {"schema": SchemaType.BIZ.value},
    )

    workline_id: int = Field(foreign_key="wes_biz.work_lines.id", sa_type=SQL_COMPAT_BIGINT)
    picking_task_id: int = Field(sa_type=SQL_COMPAT_BIGINT)
    operation_id: str = Field(min_length=1, max_length=160)
    task_id: str = Field(min_length=1, max_length=100)
    plan_revision: int
    rack_id: str = Field(min_length=1, max_length=100)
    rack_face: str = Field(min_length=1, max_length=100)
    response_evidence_id: int = Field(foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT)


class ManualPickingInboundBatchScan(EnterpriseMixin, DataTableMixin, table=True):
    __tablename__: ClassVar[str] = "manual_picking_inbound_batch_scans"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        UniqueConstraint("operation_id", "bin_code", name="ux_manual_picking_inbound_batch_scans_member"),
        {"schema": SchemaType.BIZ.value},
    )

    workline_id: int = Field(foreign_key="wes_biz.work_lines.id", sa_type=SQL_COMPAT_BIGINT)
    operation_id: str = Field(min_length=1, max_length=160)
    bin_code: str = Field(min_length=1, max_length=100)


__all__ = ["ManualPickingInboundBatch", "ManualPickingInboundBatchScan"]
