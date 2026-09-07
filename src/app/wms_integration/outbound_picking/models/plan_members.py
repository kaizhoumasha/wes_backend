"""PickingTask 不可变来源身份；记录计划，不表示物理执行完成。"""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import BigInteger, CheckConstraint, Index
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
from src.database.schema_conf import SchemaType


class DirectPickExecution(EnterpriseMixin, DataTableMixin, table=True):
    __tablename__: ClassVar[str] = "direct_pick_executions"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index(
            "ux_direct_pick_source",
            "picking_task_id",
            "rack_id",
            "rack_face",
            "slot_id",
            unique=True,
        ),
        CheckConstraint("plan_revision >= 1", name="direct_pick_revision_positive"),
        CheckConstraint("length(rack_face) > 0", name="direct_pick_face_nonempty"),
        {"schema": SchemaType.BIZ.value},
    )
    picking_task_id: int = Field(foreign_key="wes_biz.picking_tasks.id", sa_type=SQL_COMPAT_BIGINT)
    rack_id: str = Field(max_length=100)
    rack_face: str = Field(min_length=1, max_length=10)
    slot_id: str = Field(max_length=100)
    plan_revision: int = Field(sa_type=BigInteger)
    source_evidence_id: int = Field(foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT)


class PickingTaskBinSourceRack(EnterpriseMixin, DataTableMixin, table=True):
    __tablename__: ClassVar[str] = "picking_task_bin_source_racks"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index(
            "ux_picking_bin_source",
            "picking_task_id",
            "rack_id",
            "rack_face",
            unique=True,
        ),
        CheckConstraint("plan_revision >= 1", name="picking_bin_source_revision_positive"),
        CheckConstraint("length(rack_face) > 0", name="picking_bin_source_face_nonempty"),
        {"schema": SchemaType.BIZ.value},
    )
    picking_task_id: int = Field(foreign_key="wes_biz.picking_tasks.id", sa_type=SQL_COMPAT_BIGINT)
    rack_id: str = Field(max_length=100)
    rack_face: str = Field(min_length=1, max_length=10)
    plan_revision: int = Field(sa_type=BigInteger)
    source_evidence_id: int = Field(foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT)
