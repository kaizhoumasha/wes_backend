"""任务实际转运架的当前轮次及原离场义务，不依赖已关闭单盘历史。"""

from datetime import datetime
from typing import ClassVar

from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
from src.database.schema_conf import SchemaType


class AutomaticPickingFinalTargetRack(EnterpriseMixin, DataTableMixin, table=True):
    __tablename__: ClassVar[str] = "automatic_picking_final_target_racks"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = ({"schema": SchemaType.BIZ.value},)
    workline_id: int = Field(foreign_key="wes_biz.work_lines.id", sa_type=SQL_COMPAT_BIGINT)
    picking_task_id: int = Field(foreign_key="wes_biz.picking_tasks.id", sa_type=SQL_COMPAT_BIGINT, unique=True)
    rack_id: str = Field(max_length=100)
    root_transport_task_id: str = Field(max_length=80)
    arrival_transport_task_id: str | None = Field(
        default=None, max_length=80, foreign_key="wes_biz.transport_tasks.transport_task_id"
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
