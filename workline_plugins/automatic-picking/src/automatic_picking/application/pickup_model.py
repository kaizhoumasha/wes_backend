"""扫码前的取盘身份；来源与 ACCEPT 内容只保存在原 Evidence。"""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
from src.database.schema_conf import SchemaType


class AutomaticPickingDirectPick(EnterpriseMixin, DataTableMixin, table=True):
    """退料有效成员的当前进度；完成事实独立于可清理的单盘历史。"""

    __tablename__: ClassVar[str] = "automatic_picking_direct_picks"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = ({"schema": SchemaType.BIZ.value},)
    workline_id: int = Field(foreign_key="wes_biz.work_lines.id", sa_type=SQL_COMPAT_BIGINT)
    picking_task_id: int = Field(foreign_key="wes_biz.picking_tasks.id", sa_type=SQL_COMPAT_BIGINT)
    member_id: int = Field(foreign_key="wes_biz.direct_pick_executions.id", sa_type=SQL_COMPAT_BIGINT, unique=True)
    completed_evidence_id: int | None = Field(
        default=None, foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT
    )


class AutomaticPickingPickup(EnterpriseMixin, DataTableMixin, table=True):
    __tablename__: ClassVar[str] = "automatic_picking_pickups"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        UniqueConstraint("work_id", "authorization_evidence_id", name="ux_automatic_pickup_authorization"),
        CheckConstraint("(work_id IS NULL) <> (direct_pick_id IS NULL)", name="automatic_pickup_source_owner"),
        CheckConstraint(
            "(put_result_evidence_id IS NULL AND movement_confirmation_id IS NULL) OR "
            "(put_command_code IS NOT NULL AND put_result_evidence_id IS NOT NULL AND movement_confirmation_id IS NOT NULL)",
            name="automatic_pickup_placement_pair",
        ),
        {"schema": SchemaType.BIZ.value},
    )
    pickup_id: str = Field(max_length=36, unique=True)
    work_id: int | None = Field(
        default=None, foreign_key="wes_biz.automatic_picking_works.id", sa_type=SQL_COMPAT_BIGINT
    )
    direct_pick_id: int | None = Field(
        default=None, foreign_key="wes_biz.automatic_picking_direct_picks.id", sa_type=SQL_COMPAT_BIGINT, unique=True
    )
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
    target_departure_confirmation_id: int | None = Field(
        default=None, foreign_key="wes_biz.wms_confirmations.id", sa_type=SQL_COMPAT_BIGINT
    )
    target_departure_round_id: str | None = Field(default=None, max_length=120)
    target_departure_retry_at: datetime | None = None
    target_departure_transport_task_id: str | None = Field(
        default=None, max_length=80, foreign_key="wes_biz.transport_tasks.transport_task_id"
    )
    target_inbound_transport_task_id: str | None = Field(
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
