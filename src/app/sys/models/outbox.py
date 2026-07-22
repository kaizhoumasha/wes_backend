"""系统级发件箱模型。

所有面向外部硬件系统的异步副作用都从这里派发：

    Domain Service -> DispatchEnvelope -> SystemOutbox -> SystemOutboxEngine
        -> endpoint/device sender -> WMS/RCS/AGV/CTU -> callback

SystemOutbox 采用 at-least-once 派发语义。下游请求必须携带稳定的
dispatch_key/request_id，并由对方按该键幂等处理重复请求。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime  # noqa: TC003
from enum import Enum
from typing import Any, ClassVar, Literal, cast

from sqlalchemy import JSON, BigInteger, Column, ForeignKey, Index, Text, text
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field

from src.core.mixins import BaseMixin, DataTableMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType


class SystemOutboxStatus(str, Enum):
    """唯一 transport 状态。

    NEW -> DISPATCHING -+-> SENT
                        +-> RETRY_WAIT -> DISPATCHING
                        +-> FAILED / UNKNOWN / CANCELLED
    UNKNOWN 是不可自动重试的送达歧义，不代表业务成功或失败。
    """

    NEW = "NEW"
    DISPATCHING = "DISPATCHING"
    RETRY_WAIT = "RETRY_WAIT"
    SENT = "SENT"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
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


class OperationCompletionPolicy(str, Enum):
    """Operation 完成确认策略。"""

    CALLBACK_TRUSTED = "CALLBACK_TRUSTED"
    RESOURCE_PROJECTION_REQUIRED = "RESOURCE_PROJECTION_REQUIRED"
    CALLBACK_PLUS_RECONCILIATION = "CALLBACK_PLUS_RECONCILIATION"


@dataclass(frozen=True)
class DispatchEnvelope:
    """领域 gateway 交给 SystemOutbox 的统一派发包络。"""

    dispatch_key: str
    dispatch_type: SystemOutboxDispatchType
    target_type: SystemOutboxTargetType
    target_code: str
    payload_json: dict[str, Any]
    operation_domain: str
    operation_key: str | None = None
    workline_id: int | None = None
    session_id: int | None = None
    device_id: int | None = None
    trace_id: str | None = None


class SystemOutboxBase(BaseMixin):
    """系统级发件箱基础字段。"""

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
    device_id: int | None = Field(
        default=None,
        index=True,
        foreign_key="wes_biz.devices.id",
        description="可选关联 Device.id，用于物理设备 FIFO",
    )
    operation_domain: str = Field(default="WORKLINE", max_length=50, index=True, description="操作域")
    operation_key: str | None = Field(default=None, max_length=240, index=True, description="操作幂等键")
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
    target_code: str = Field(min_length=1, max_length=240, index=True, description="目标逻辑编码")
    payload_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="派发负载")
    status: SystemOutboxStatus = Field(
        default=SystemOutboxStatus.NEW,
        index=True,
        sa_type=cast("Any", SQLAEnum(SystemOutboxStatus, native_enum=False, create_constraint=True, length=50)),
        description="派发状态",
    )
    attempt_count: int = Field(default=0, ge=0, description="尝试次数")
    next_retry_at: datetime | None = Field(default=None, index=True, description="下次重试时间或派发租约截止时间")
    last_error: str | None = Field(default=None, sa_column=Column(Text), description="最后错误")
    sent_at: datetime | None = Field(default=None, description="发送时间")
    finished_at: datetime | None = Field(default=None, index=True, description="结束时间")
    trace_id: str | None = Field(default=None, max_length=100, index=True, description="Trace ID")
    blocked_by_reconciliation_session_id: int | None = Field(
        default=None,
        index=True,
        description="阻断该 outbox 的 runtime reconciliation owner session ID",
    )
    blocked_by_runtime_hold_id: int | None = Field(
        default=None,
        sa_column=Column(
            BigInteger,
            ForeignKey(
                "wes_biz.runtime_holds.id",
                name="fk_system_outbox_blocked_by_runtime_hold_id",
                use_alter=True,
            ),
            nullable=True,
            index=True,
        ),
        description="阻断该 outbox 的 RuntimeHold.id",
    )
    blocked_device_id: int | None = Field(default=None, index=True, description="阻断相关设备 ID")
    blocked_workline_id: int | None = Field(default=None, index=True, description="阻断相关工作线 ID")
    blocked_reason: str | None = Field(default=None, max_length=100, description="阻断原因")
    blocked_at: datetime | None = Field(default=None, description="资源等待起始时间")
    last_blocked_check_at: datetime | None = Field(default=None, description="最近一次资源等待探测时间")
    blocked_check_count: int = Field(default=0, ge=0, description="资源等待探测次数")
    blocked_detail_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
        description="资源等待诊断摘要",
    )


class SystemOutbox(SystemOutboxBase, DataTableMixin, table=True):
    """系统级发件箱。"""

    __tablename__: ClassVar[Literal["system_outbox"]] = "system_outbox"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index("ux_system_outbox_dispatch_key", "dispatch_key", unique=True),
        Index("ix_system_outbox_status_retry_created", "status", "next_retry_at", "created_at"),
        Index("ix_system_outbox_domain_operation", "operation_domain", "operation_key"),
        Index("ix_system_outbox_context_status", "workline_id", "session_id", "status"),
        Index("ix_system_outbox_blocked_release", "blocked_reason", "blocked_device_id", "blocked_workline_id"),
        Index(
            "ix_system_outbox_blocked_device_head_probe",
            "operation_domain",
            "status",
            "dispatch_type",
            "blocked_reason",
            "last_blocked_check_at",
            "blocked_device_id",
            "target_code",
            "created_at",
            postgresql_where=text("status = 'RETRY_WAIT' AND dispatch_type = 'DEVICE_COMMAND'"),
            sqlite_where=text("status = 'RETRY_WAIT' AND dispatch_type = 'DEVICE_COMMAND'"),
        ),
        Index("ix_system_outbox_retention", "status", "finished_at"),
        Index(
            "ix_system_outbox_device_fifo",
            "dispatch_type",
            "device_id",
            "target_code",
            "status",
            "created_at",
            postgresql_where=text("dispatch_type = 'DEVICE_COMMAND'"),
            sqlite_where=text("dispatch_type = 'DEVICE_COMMAND'"),
        ),
        {"schema": SchemaType.BIZ.value},
    )


class SystemOutboxCreate(ModelFactory(SystemOutboxBase).for_create()):
    """系统级发件箱创建 Schema。"""


class SystemOutboxUpdate(ModelFactory(SystemOutboxBase).for_update()):
    """系统级发件箱更新 Schema。"""


__all__ = [
    "DispatchEnvelope",
    "OperationCompletionPolicy",
    "SystemOutbox",
    "SystemOutboxBase",
    "SystemOutboxCreate",
    "SystemOutboxDispatchType",
    "SystemOutboxStatus",
    "SystemOutboxTargetType",
    "SystemOutboxUpdate",
]
