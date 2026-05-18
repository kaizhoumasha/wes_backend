"""
作业线发件箱模型 (Workline Outbox)

所有副作用的统一派发出口，管理设备指令和外部调用。
遵循白皮书 8.8 节规范。

相关文档:
- 白皮书: @docs/workline_plugin_architecture_design.md
"""

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar, cast

from sqlalchemy import JSON, BigInteger, Column, ForeignKey, Text
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field, Relationship

from src.core.mixins import BaseMixin, DataTableMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType

if TYPE_CHECKING:
    from src.app.workline.models.session import WorklineSession


# ==================== 枚举定义 ====================


class OutboxStatus(str, Enum):
    """发件箱消息状态枚举 (白皮书 8.8)"""

    NEW = "NEW"  # 新消息
    DISPATCHING = "DISPATCHING"  # 派发中
    SENT = "SENT"  # 已发送
    BLOCKED_RESOURCE = "BLOCKED_RESOURCE"  # 因运行时资源隔离暂缓派发
    FAILED = "FAILED"  # 失败
    CANCELLED = "CANCELLED"  # 已取消


class DispatchType(str, Enum):
    """派发类型枚举 (白皮书 8.8)"""

    DEVICE_COMMAND = "DEVICE_COMMAND"  # 设备指令
    EXTERNAL_HTTP = "EXTERNAL_HTTP"  # 外部 HTTP 调用
    INTERNAL_SIGNAL = "INTERNAL_SIGNAL"  # 内部信号


class TargetType(str, Enum):
    """目标类型枚举"""

    DEVICE = "DEVICE"  # 设备
    HTTP_ENDPOINT = "HTTP_ENDPOINT"  # HTTP 端点
    INTERNAL_SERVICE = "INTERNAL_SERVICE"  # 内部服务


# ==================== 基础字段 (用于 Schema 复用) ====================


class WorklineOutboxBase(BaseMixin):
    """发件箱基础字段 - 用于 Schema 复用"""

    # 会话关联
    session_id: int | None = Field(
        default=None,
        index=True,
        foreign_key="wes_biz.workline_sessions.id",
        description="会话 ID（关联 WorklineSession.id）",
    )

    workline_id: int = Field(
        index=True,
        foreign_key="wes_biz.work_lines.id",
        description="作业线 ID（关联 WorkLine.id）",
    )

    # 派发信息
    dispatch_type: DispatchType = Field(
        index=True,
        sa_type=cast(
            "Any",
            SQLAEnum(
                DispatchType,
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
        ),
        description="派发类型",
    )

    dispatch_key: str = Field(
        max_length=200,
        unique=True,
        index=True,
        description="派发键（用于幂等和去重）",
    )

    # 目标信息
    target_type: TargetType = Field(
        index=True,
        sa_type=cast(
            "Any",
            SQLAEnum(
                TargetType,
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
        ),
        description="目标类型",
    )

    target_code: str = Field(
        max_length=100,
        description="目标编码（如设备编码、URL 路径）",
    )

    # 消息内容
    payload_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON),
        description="消息负载（JSON 格式）",
    )

    # 处理状态
    status: OutboxStatus = Field(
        default=OutboxStatus.NEW,
        index=True,
        sa_type=cast(
            "Any",
            SQLAEnum(
                OutboxStatus,
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
        ),
        description="派发状态",
    )

    attempt_count: int = Field(
        default=0,
        ge=0,
        description="尝试次数",
    )

    next_retry_at: datetime | None = Field(
        default=None,
        index=True,
        description="下次重试时间",
    )

    last_error: str | None = Field(
        default=None,
        sa_column=Column(Text),
        description="最后一次错误",
    )

    sent_at: datetime | None = Field(
        default=None,
        description="发送时间",
    )

    finished_at: datetime | None = Field(
        default=None,
        description="完成时间",
    )

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
                name="fk_workline_outbox_blocked_by_runtime_hold_id",
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


# ==================== 数据库表模型 ====================


class WorklineOutbox(
    WorklineOutboxBase,
    DataTableMixin,
    table=True,
):
    """
    作业线发件箱数据库表模型

    所有副作用的统一派发出口，管理设备指令和外部调用。

    字段说明:
    - session_id/workline_id: 关联的会话和作业线
    - dispatch_type: 派发类型（设备指令、外部 HTTP、内部信号）
    - target_type/target_code: 目标信息
    - payload_json: 消息负载
    - attempt_count/next_retry_at: 重试相关

    状态机:
        NEW → DISPATCHING → SENT
          ↓        ↓          ↓
        BLOCKED_RESOURCE / FAILED / CANCELLED

    派发类型:
    - DEVICE_COMMAND: 派发设备指令
    - EXTERNAL_HTTP: 调用外部 HTTP API
    - INTERNAL_SIGNAL: 发送内部信号
    """

    __tablename__: ClassVar[str] = "workline_outbox"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value  # 业务数据表

    # 关系定义
    session: "WorklineSession" = Relationship(
        back_populates=None,
        sa_relationship_kwargs={
            "lazy": "selectin",
            "foreign_keys": "WorklineOutbox.session_id",
            "primaryjoin": "WorklineOutbox.session_id == WorklineSession.id",
        },
    )


# ==================== 自动生成的 Schema ====================


class WorklineOutboxCreate(ModelFactory(WorklineOutboxBase).for_create()):
    """发件箱创建 Schema"""


# ==================== 导出 ====================


__all__ = [
    "DispatchType",
    "OutboxStatus",
    "TargetType",
    "WorklineOutbox",
    "WorklineOutboxBase",
    "WorklineOutboxCreate",
]
