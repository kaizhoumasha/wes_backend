"""人工确认的当前来源架面；原搬运和历史审计独立保留。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 -- SQLModel resolves this field type at runtime.
from typing import ClassVar

from sqlalchemy import CheckConstraint, Index, UniqueConstraint, text
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
from src.database.schema_conf import SchemaType


class ManualPickingSourceArrival(EnterpriseMixin, DataTableMixin, table=True):
    __tablename__: ClassVar[str] = "manual_picking_source_arrivals"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        UniqueConstraint("workline_id", "source_transport_task_id", name="ux_manual_source_arrival_transport"),
        Index(
            "ux_manual_source_arrival_current",
            "workline_id",
            unique=True,
            postgresql_where=text("is_current"),
            sqlite_where=text("is_current"),
        ),
        CheckConstraint("source_causal_token > 0 AND plan_revision > 0", name="manual_source_arrival_positive"),
        {"schema": SchemaType.BIZ.value},
    )
    is_current: bool = Field(default=True)
    workline_id: int = Field(foreign_key="wes_biz.work_lines.id", sa_type=SQL_COMPAT_BIGINT)
    picking_task_id: int = Field(foreign_key="wes_biz.picking_tasks.id", sa_type=SQL_COMPAT_BIGINT)
    source_evidence_id: int = Field(sa_type=SQL_COMPAT_BIGINT)
    source_causal_token: int = Field(sa_type=SQL_COMPAT_BIGINT)
    source_transport_task_id: str = Field(max_length=80)
    plan_revision: int
    rack_id: str = Field(min_length=1, max_length=100)
    rack_face: str = Field(min_length=1, max_length=10)
    location_code: str = Field(min_length=1, max_length=100)
    observed_at: datetime
    audit_id: int = Field(sa_type=SQL_COMPAT_BIGINT)
    confirmation_digest: str = Field(min_length=64, max_length=64)

    @property
    def arrival_face(self) -> str:
        return self.rack_face


def matches_source_arrival(fact, *, picking_task_id, source, location_code):
    return (
        fact.picking_task_id == picking_task_id
        and fact.location_code == location_code
        and fact.rack_id == source.rack_id
        and fact.rack_face == source.rack_face
        and fact.plan_revision == source.plan_revision
        and fact.source_evidence_id == source.source_evidence_id
        and getattr(source, "cancelled_evidence_id", None) is None
    )
