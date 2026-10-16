"""当前 Bin 轮次与原 work_plan 引用，不保存 Cell 库存。"""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, text
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
from src.database.schema_conf import SchemaType


class AutomaticPickingWork(EnterpriseMixin, DataTableMixin, table=True):
    __tablename__: ClassVar[str] = "automatic_picking_works"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        ForeignKeyConstraint(
            ["consumed_pickup_id"],
            ["wes_biz.automatic_picking_pickups.pickup_id"],
            name="fk_automatic_work_consumed_pickup",
            use_alter=True,
        ),
        CheckConstraint(
            "phase IN ('PLAN_PENDING','WAIT','READY','EXIT_PENDING','CLOSED','RECONCILING')",
            name="automatic_work_phase",
        ),
        Index("ux_automatic_work_scan2", "scan2_evidence_id", unique=True),
        Index(
            "ux_automatic_work_open_bin",
            "workline_id",
            "bin_code",
            unique=True,
            postgresql_where=text("phase <> 'CLOSED' AND archived_at IS NULL"),
            sqlite_where=text("phase <> 'CLOSED' AND archived_at IS NULL"),
        ),
        {"schema": SchemaType.BIZ.value},
    )
    workline_id: int = Field(foreign_key="wes_biz.work_lines.id", sa_type=SQL_COMPAT_BIGINT)
    picking_task_id: int = Field(foreign_key="wes_biz.picking_tasks.id", sa_type=SQL_COMPAT_BIGINT)
    feed_id: int = Field(foreign_key="wes_biz.automatic_picking_feeds.id", sa_type=SQL_COMPAT_BIGINT)
    bin_code: str = Field(max_length=100)
    scan2_evidence_id: int = Field(foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT)
    scanned_at: int = Field(sa_type=SQL_COMPAT_BIGINT)
    operation_id: str = Field(max_length=36, unique=True)
    phase: str = Field(max_length=30)
    response_evidence_id: int | None = Field(
        default=None, foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT
    )
    authorization_evidence_id: int | None = Field(
        default=None, foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT
    )
    consumed_pickup_id: str | None = Field(default=None, max_length=36)
    # 当前栈顶的可靠事实随 Work 保留，后继恢复不查询已闭合 Pickup/Command。
    top_removed_evidence_id: int | None = Field(
        default=None, foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT
    )
    exit_command_code: str | None = Field(
        default=None, foreign_key="wes_biz.device_commands.command_code", max_length=80, unique=True
    )
    retry_at: datetime | None = None
    archived_at: datetime | None = None
