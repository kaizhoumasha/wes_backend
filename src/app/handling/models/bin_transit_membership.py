"""Handling 料箱流水线队列 membership 投影。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from enum import Enum
from typing import Any, ClassVar, Literal, cast

from sqlalchemy import JSON, Column, Index, text
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field

from src.core.mixins import BaseMixin, DataTableMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType
from src.utils.timezone import timezone


class BinTransitQueue(str, Enum):
    """SMT 料箱流水线队列枚举。"""

    INFEED_BUFFER_QUEUE = "INFEED_BUFFER_QUEUE"
    ENTRY_SCAN_QUEUE = "ENTRY_SCAN_QUEUE"
    WORKSTATION_WAIT_QUEUE = "WORKSTATION_WAIT_QUEUE"
    WORKSTATION_ACTIVE = "WORKSTATION_ACTIVE"
    EXIT_ROUTING_SCAN_QUEUE = "EXIT_ROUTING_SCAN_QUEUE"
    RETURN_SCAN_QUEUE = "RETURN_SCAN_QUEUE"
    RETURN_WAIT_QUEUE = "RETURN_WAIT_QUEUE"
    NG_REJECT_QUEUE = "NG_REJECT_QUEUE"


class BinTransitMembershipStatus(str, Enum):
    """料箱队列 membership 状态。"""

    ACTIVE = "ACTIVE"
    LEFT = "LEFT"
    RECONCILING = "RECONCILING"


class BinTransitMembershipBase(BaseMixin):
    """料箱队列 membership 基础字段。"""

    bin_code: str | None = Field(default=None, max_length=100, index=True, description="真实料箱编码")
    placeholder_key: str | None = Field(default=None, max_length=240, index=True, description="未扫码临时占位键")
    workline_id: int | None = Field(
        default=None,
        sa_type=SQL_COMPAT_BIGINT,
        index=True,
        foreign_key="wes_biz.work_lines.id",
        description="关联 WorkLine.id",
    )
    workline_code: str | None = Field(default=None, max_length=50, index=True, description="工作线编码")
    current_queue: BinTransitQueue = Field(
        index=True,
        sa_type=cast("Any", SQLAEnum(BinTransitQueue, native_enum=False, create_constraint=True, length=80)),
        description="当前队列",
    )
    membership_status: BinTransitMembershipStatus = Field(
        default=BinTransitMembershipStatus.ACTIVE,
        index=True,
        sa_type=cast(
            "Any",
            SQLAEnum(BinTransitMembershipStatus, native_enum=False, create_constraint=True, length=50),
        ),
        description="membership 状态",
    )
    handling_operation_id: int | None = Field(
        default=None,
        sa_type=SQL_COMPAT_BIGINT,
        index=True,
        foreign_key="wes_biz.handling_operations.id",
        description="证据关联 HandlingOperation.id",
    )
    handling_move_id: int | None = Field(
        default=None,
        sa_type=SQL_COMPAT_BIGINT,
        index=True,
        foreign_key="wes_biz.handling_operation_moves.id",
        description="证据关联 HandlingMove.id",
    )
    trace_id: str | None = Field(default=None, max_length=100, index=True, description="Trace ID")
    workline_session_id: int | None = Field(
        default=None,
        sa_type=SQL_COMPAT_BIGINT,
        index=True,
        foreign_key="wes_biz.workline_sessions.id",
        description="关联 WorklineSession.id",
    )
    entered_at: datetime = Field(default_factory=timezone.now_for_db, index=True, description="进入队列时间")
    left_at: datetime | None = Field(default=None, index=True, description="离开队列时间")
    evidence_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, default=dict),
        description="投影证据",
    )


class BinTransitMembership(BinTransitMembershipBase, DataTableMixin, table=True):
    """料箱流水线队列 membership active/history 投影视图。"""

    __tablename__: ClassVar[Literal["bin_transit_memberships"]] = (  # pyright: ignore[reportIncompatibleVariableOverride]
        "bin_transit_memberships"
    )
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index(
            "ux_bin_transit_memberships_active_bin",
            "bin_code",
            unique=True,
            postgresql_where=text("bin_code IS NOT NULL AND left_at IS NULL"),
            sqlite_where=text("bin_code IS NOT NULL AND left_at IS NULL"),
        ),
        Index(
            "ux_bin_transit_memberships_active_placeholder",
            "placeholder_key",
            unique=True,
            postgresql_where=text("placeholder_key IS NOT NULL AND left_at IS NULL"),
            sqlite_where=text("placeholder_key IS NOT NULL AND left_at IS NULL"),
        ),
        Index("ix_bin_transit_memberships_workline_queue", "workline_id", "current_queue"),
        Index("ix_bin_transit_memberships_session_entered", "workline_session_id", "entered_at"),
        Index("ix_bin_transit_memberships_trace_entered", "trace_id", "entered_at"),
        {"schema": SchemaType.BIZ.value},
    )


class BinTransitMembershipCreate(ModelFactory(BinTransitMembershipBase).for_create()):
    """料箱队列 membership 创建 Schema。"""


class BinTransitMembershipResponse(BinTransitMembershipBase):
    """料箱队列 membership 响应 Schema。"""

    id: int
    created_at: datetime
    updated_at: datetime | None = None


__all__ = [
    "BinTransitMembership",
    "BinTransitMembershipBase",
    "BinTransitMembershipCreate",
    "BinTransitMembershipResponse",
    "BinTransitMembershipStatus",
    "BinTransitQueue",
]
