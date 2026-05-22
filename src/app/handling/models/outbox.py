"""系统级发件箱模型。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from enum import Enum
from typing import Any, ClassVar, Literal, cast

from sqlalchemy import JSON, Column, Index, Text
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field

from src.core.mixins import BaseMixin, DataTableMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType


class SystemOutboxStatus(str, Enum):
    """系统级发件箱状态。"""

    NEW = "NEW"
    DISPATCHING = "DISPATCHING"
    SENT = "SENT"
    BLOCKED_RESOURCE = "BLOCKED_RESOURCE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class SystemOutboxDispatchType(str, Enum):
    """系统级派发类型。"""

    DEVICE_COMMAND = "DEVICE_COMMAND"
    EXTERNAL_HTTP = "EXTERNAL_HTTP"
    INTERNAL_SIGNAL = "INTERNAL_SIGNAL"


class SystemOutboxTargetType(str, Enum):
    """系统级派发目标类型。"""

    DEVICE = "DEVICE"
    HTTP_ENDPOINT = "HTTP_ENDPOINT"
    INTERNAL_SERVICE = "INTERNAL_SERVICE"


class SystemOutboxBase(BaseMixin):
    """系统级发件箱基础字段。"""

    operation_id: int | None = Field(
        default=None,
        index=True,
        foreign_key="wes_biz.handling_operations.id",
        description="关联 HandlingOperation.id",
    )
    session_id: int | None = Field(
        default=None,
        index=True,
        foreign_key="wes_biz.workline_sessions.id",
        description="可选关联 WorklineSession.id",
    )
    workline_id: int | None = Field(
        default=None,
        index=True,
        foreign_key="wes_biz.work_lines.id",
        description="可选关联 WorkLine.id",
    )
    dispatch_type: SystemOutboxDispatchType = Field(
        index=True,
        sa_type=cast(
            "Any",
            SQLAEnum(SystemOutboxDispatchType, native_enum=False, create_constraint=True, length=50),
        ),
        description="派发类型",
    )
    dispatch_key: str = Field(min_length=1, max_length=240, index=True, description="派发幂等键")
    target_type: SystemOutboxTargetType = Field(
        index=True,
        sa_type=cast(
            "Any",
            SQLAEnum(SystemOutboxTargetType, native_enum=False, create_constraint=True, length=50),
        ),
        description="目标类型",
    )
    target_code: str = Field(min_length=1, max_length=240, index=True, description="目标编码")
    payload_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="派发负载")
    status: SystemOutboxStatus = Field(
        default=SystemOutboxStatus.NEW,
        index=True,
        sa_type=cast("Any", SQLAEnum(SystemOutboxStatus, native_enum=False, create_constraint=True, length=50)),
        description="派发状态",
    )
    attempt_count: int = Field(default=0, ge=0, description="尝试次数")
    next_retry_at: datetime | None = Field(default=None, index=True, description="下次重试时间")
    last_error: str | None = Field(default=None, sa_column=Column(Text), description="最后错误")
    sent_at: datetime | None = Field(default=None, description="发送时间")
    finished_at: datetime | None = Field(default=None, description="结束时间")
    trace_id: str | None = Field(default=None, max_length=100, index=True, description="Trace ID")


class SystemOutbox(SystemOutboxBase, DataTableMixin, table=True):
    """系统级发件箱。"""

    __tablename__: ClassVar[Literal["system_outbox"]] = "system_outbox"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index("ux_system_outbox_dispatch_key", "dispatch_key", unique=True),
        Index("ix_system_outbox_status_retry", "status", "next_retry_at"),
        Index("ix_system_outbox_operation_status", "operation_id", "status"),
        {"schema": SchemaType.BIZ.value},
    )


class SystemOutboxCreate(ModelFactory(SystemOutboxBase).for_create()):
    """系统级发件箱创建 Schema。"""


class SystemOutboxUpdate(ModelFactory(SystemOutboxBase).for_update()):
    """系统级发件箱更新 Schema。"""


__all__ = [
    "SystemOutbox",
    "SystemOutboxBase",
    "SystemOutboxCreate",
    "SystemOutboxDispatchType",
    "SystemOutboxStatus",
    "SystemOutboxTargetType",
    "SystemOutboxUpdate",
]
