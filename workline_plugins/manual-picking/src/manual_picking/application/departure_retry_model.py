"""本轮货架清退拒绝后的持久退避游标，不代表物理任务终态。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - SQLModel resolves persisted annotations
from typing import ClassVar

from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
from src.database.schema_conf import SchemaType


class ManualPickingDepartureRetry(EnterpriseMixin, DataTableMixin, table=True):
    __tablename__: ClassVar[str] = "manual_picking_departure_retries"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        UniqueConstraint("workline_id", "scope_identity", "rack_id", "step", name="ux_manual_departure_retry_scope"),
        CheckConstraint(
            "retry_count BETWEEN 1 AND 7 AND last_transport_causal_token > 0", name="manual_departure_retry_positive"
        ),
        {"schema": SchemaType.BIZ.value},
    )

    workline_id: int = Field(foreign_key="wes_biz.work_lines.id", sa_type=SQL_COMPAT_BIGINT)
    scope_identity: str = Field(min_length=1, max_length=256)
    rack_id: str = Field(min_length=1, max_length=160)
    step: str = Field(min_length=1, max_length=96)
    retry_count: int = Field(ge=1, le=7)
    last_transport_task_id: str = Field(min_length=1, max_length=80)
    last_transport_causal_token: int = Field(sa_type=SQL_COMPAT_BIGINT)
    next_attempt_at: datetime
    decision_operation_id: str | None = Field(default=None, max_length=160)
