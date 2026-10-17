"""当前回架分配的恢复根；成员只保留 execution 标识，不依赖已关闭 Return。"""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import JSON, CheckConstraint
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
from src.database.schema_conf import SchemaType


class AutomaticPickingReturnAllocation(EnterpriseMixin, DataTableMixin, table=True):
    __tablename__: ClassVar[str] = "automatic_picking_return_allocations"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        CheckConstraint(
            "(response_evidence_id IS NULL AND selected_count IS NULL) OR "
            "(response_evidence_id IS NOT NULL AND selected_count IS NOT NULL AND selected_count BETWEEN 0 AND 4)",
            name="automatic_return_allocation_response",
        ),
        {"schema": SchemaType.BIZ.value},
    )
    workline_id: int = Field(foreign_key="wes_biz.work_lines.id", sa_type=SQL_COMPAT_BIGINT)
    arrival_evidence_id: int = Field(foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT)
    rack_id: str = Field(max_length=100)
    rack_face: str = Field(max_length=10)
    operation_id: str = Field(max_length=36, unique=True)
    return_ids: list[int] = Field(sa_type=JSON)
    response_evidence_id: int | None = Field(
        default=None, foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT
    )
    selected_count: int | None = None
    transport_task_id: str | None = Field(
        default=None, foreign_key="wes_biz.transport_tasks.transport_task_id", max_length=80
    )
    transport_result_evidence_id: int | None = Field(
        default=None, foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT
    )
    handed_off_at: datetime | None = None
    settled_at: datetime | None = None
    archived_at: datetime | None = None
