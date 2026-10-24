"""来源成员的当前投料游标；冻结清单与动作正文仍归 Evidence/Transport。"""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import JSON, CheckConstraint, UniqueConstraint
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
from src.database.schema_conf import SchemaType


class AutomaticPickingFeed(EnterpriseMixin, DataTableMixin, table=True):
    __tablename__: ClassVar[str] = "automatic_picking_feeds"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        UniqueConstraint("source_member_id", name="ux_automatic_feed_member"),
        CheckConstraint('"offset" >= 0', name="automatic_feed_offset"),
        CheckConstraint(
            "phase IN ('REQUESTED','ALLOCATED','FEEDING','FEED_COMPLETE','CANCELLED','RECONCILING')",
            name="automatic_feed_phase",
        ),
        CheckConstraint(
            "(inbound_client_request_id IS NULL AND round_started_at IS NULL AND round_causal_token IS NULL "
            "AND source_arrival_evidence_id IS NULL) OR "
            "(inbound_client_request_id IS NOT NULL AND round_started_at IS NOT NULL "
            "AND round_causal_token IS NOT NULL AND round_causal_token > 0 AND source_arrival_evidence_id IS NOT NULL)",
            name="automatic_feed_source_round",
        ),
        {"schema": SchemaType.BIZ.value},
    )
    workline_id: int = Field(foreign_key="wes_biz.work_lines.id", sa_type=SQL_COMPAT_BIGINT)
    picking_task_id: int = Field(foreign_key="wes_biz.picking_tasks.id", sa_type=SQL_COMPAT_BIGINT)
    source_member_id: int = Field(foreign_key="wes_biz.picking_task_bin_source_racks.id", sa_type=SQL_COMPAT_BIGINT)
    completed_source_member_ids: list[int] = Field(default_factory=list, sa_type=JSON)
    operation_id: str = Field(max_length=36, unique=True)
    phase: str = Field(default="REQUESTED", max_length=30)
    response_evidence_id: int | None = Field(
        default=None, foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT
    )
    offset: int = Field(default=0)
    transport_task_id: str | None = Field(
        default=None, foreign_key="wes_biz.transport_tasks.transport_task_id", max_length=80
    )
    # 换面交接后的当前轮次，不依赖可清理的原进场与前阶段。
    inbound_client_request_id: str | None = Field(default=None, max_length=120)
    round_started_at: datetime | None = None
    round_causal_token: int | None = Field(default=None, sa_type=SQL_COMPAT_BIGINT)
    source_arrival_evidence_id: int | None = Field(
        default=None, foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT
    )
    archived_at: datetime | None = None


class AutomaticPickingFeedScan(EnterpriseMixin, DataTableMixin, table=True):
    __tablename__: ClassVar[str] = "automatic_picking_feed_scans"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        UniqueConstraint("feed_id", "bin_code", name="ux_automatic_feed_scan_member"),
        {"schema": SchemaType.BIZ.value},
    )
    feed_id: int = Field(foreign_key="wes_biz.automatic_picking_feeds.id", sa_type=SQL_COMPAT_BIGINT)
    bin_code: str = Field(max_length=100)
    evidence_id: int = Field(foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT)
    # 当前批次成员的消费标记，准入不依赖可清理的 CLOSED Work 历史。
    work_admitted: bool = Field(default=False)
    scan1_command_code: str | None = Field(
        default=None, foreign_key="wes_biz.device_commands.command_code", max_length=80
    )
