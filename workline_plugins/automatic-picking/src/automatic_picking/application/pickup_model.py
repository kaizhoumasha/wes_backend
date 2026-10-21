"""扫码前的取盘身份；来源与 ACCEPT 内容只保存在原 Evidence。"""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
from src.database.schema_conf import SchemaType


class AutomaticPickingPickup(EnterpriseMixin, DataTableMixin, table=True):
    __tablename__: ClassVar[str] = "automatic_picking_pickups"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        UniqueConstraint("work_id", "authorization_evidence_id", name="ux_automatic_pickup_authorization"),
        CheckConstraint(
            "(put_result_evidence_id IS NULL AND movement_confirmation_id IS NULL) OR "
            "(put_command_code IS NOT NULL AND put_result_evidence_id IS NOT NULL AND movement_confirmation_id IS NOT NULL)",
            name="automatic_pickup_placement_pair",
        ),
        {"schema": SchemaType.BIZ.value},
    )
    pickup_id: str = Field(max_length=36, unique=True)
    work_id: int = Field(foreign_key="wes_biz.automatic_picking_works.id", sa_type=SQL_COMPAT_BIGINT)
    authorization_evidence_id: int = Field(foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT)
    command_code: str = Field(max_length=80, foreign_key="wes_biz.device_commands.command_code", unique=True)
    material_execution_id: int | None = Field(
        default=None, foreign_key="wes_biz.material_executions.id", sa_type=SQL_COMPAT_BIGINT, unique=True
    )
    accept_evidence_id: int | None = Field(
        default=None, foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT, unique=True
    )
    material_confirmation_id: int | None = Field(
        default=None, foreign_key="wes_biz.wms_confirmations.id", sa_type=SQL_COMPAT_BIGINT
    )
    material_retry_at: datetime | None = None
    target_rotation_transport_task_id: str | None = Field(
        default=None, max_length=80, foreign_key="wes_biz.transport_tasks.transport_task_id", unique=True
    )
    target_departure_transport_task_id: str | None = Field(
        default=None, max_length=80, foreign_key="wes_biz.transport_tasks.transport_task_id"
    )
    put_command_code: str | None = Field(
        default=None, max_length=80, foreign_key="wes_biz.device_commands.command_code", unique=True
    )
    put_result_evidence_id: int | None = Field(
        default=None, foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT, unique=True
    )
    movement_confirmation_id: int | None = Field(
        default=None, foreign_key="wes_biz.wms_confirmations.id", sa_type=SQL_COMPAT_BIGINT, unique=True
    )
