"""退料架当前轮次和任务当前确认引用；业务状态仍归原成员/任务。"""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import CheckConstraint
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
from src.database.schema_conf import SchemaType


class AutomaticPickingReturnRack(EnterpriseMixin, DataTableMixin, table=True):
    __tablename__: ClassVar[str] = "automatic_picking_return_racks"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = ({"schema": SchemaType.BIZ.value},)
    workline_id: int = Field(foreign_key="wes_biz.work_lines.id", sa_type=SQL_COMPAT_BIGINT)
    picking_task_id: int = Field(foreign_key="wes_biz.picking_tasks.id", sa_type=SQL_COMPAT_BIGINT)
    rack_id: str = Field(max_length=100)
    root_transport_task_id: str = Field(max_length=80, unique=True)
    arrival_transport_task_id: str | None = Field(
        default=None, max_length=80, foreign_key="wes_biz.transport_tasks.transport_task_id"
    )
    arrival_confirmation_id: int | None = Field(
        default=None, foreign_key="wes_biz.wms_confirmations.id", sa_type=SQL_COMPAT_BIGINT
    )
    rotation_transport_task_id: str | None = Field(
        default=None, max_length=80, foreign_key="wes_biz.transport_tasks.transport_task_id", unique=True
    )
    departure_confirmation_id: int | None = Field(
        default=None, foreign_key="wes_biz.wms_confirmations.id", sa_type=SQL_COMPAT_BIGINT
    )
    departure_retry_at: datetime | None = None
    departure_transport_task_id: str | None = Field(
        default=None, max_length=80, foreign_key="wes_biz.transport_tasks.transport_task_id", unique=True
    )
    closed_evidence_id: int | None = Field(
        default=None, foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT
    )


class AutomaticPickingTaskCompletion(EnterpriseMixin, DataTableMixin, table=True):
    __tablename__: ClassVar[str] = "automatic_picking_task_completions"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        CheckConstraint(
            "confirmation_id IS NOT NULL OR first_plan_due_at IS NOT NULL", name="automatic_completion_has_owner"
        ),
        {"schema": SchemaType.BIZ.value},
    )
    workline_id: int = Field(foreign_key="wes_biz.work_lines.id", sa_type=SQL_COMPAT_BIGINT)
    picking_task_id: int = Field(foreign_key="wes_biz.picking_tasks.id", sa_type=SQL_COMPAT_BIGINT, unique=True)
    confirmation_id: int | None = Field(
        default=None, foreign_key="wes_biz.wms_confirmations.id", sa_type=SQL_COMPAT_BIGINT
    )
    first_plan_due_at: datetime | None = None
