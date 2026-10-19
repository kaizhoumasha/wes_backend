"""WorkLine 当前排空决定；正文与 reservation 仍只保存在 Confirmation/Evidence。"""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import JSON, CheckConstraint, Index, UniqueConstraint, text
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
from src.database.schema_conf import SchemaType


class AutomaticPickingDrain(EnterpriseMixin, DataTableMixin, table=True):
    __tablename__: ClassVar[str] = "automatic_picking_drains"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        CheckConstraint("retry_at IS NULL OR response_evidence_id IS NOT NULL", name="automatic_drain_wait_basis"),
        Index(
            "ix_automatic_drain_current",
            "workline_id",
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
            sqlite_where=text("archived_at IS NULL"),
        ),
        {"schema": SchemaType.BIZ.value},
    )
    workline_id: int = Field(foreign_key="wes_biz.work_lines.id", sa_type=SQL_COMPAT_BIGINT)
    operation_id: str = Field(max_length=36, unique=True)
    return_ids: list[int] = Field(sa_type=JSON)
    response_evidence_id: int | None = Field(
        default=None, foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT
    )
    retry_at: datetime | None = None
    archived_at: datetime | None = None


class AutomaticPickingDrainRack(EnterpriseMixin, DataTableMixin, table=True):
    """每架当前执行引用；不复制 reservation、Transport 正文或位置。"""

    __tablename__: ClassVar[str] = "automatic_picking_drain_racks"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        UniqueConstraint("drain_id", "rack_id", name="ux_automatic_drain_rack"),
        {"schema": SchemaType.BIZ.value},
    )
    drain_id: int = Field(foreign_key="wes_biz.automatic_picking_drains.id", sa_type=SQL_COMPAT_BIGINT)
    rack_id: str = Field(max_length=160)
    binding_id: int = Field(
        foreign_key="wes_biz.transport_decision_bindings.id", unique=True, sa_type=SQL_COMPAT_BIGINT
    )
    arrival_evidence_id: int | None = Field(
        default=None, foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT
    )
