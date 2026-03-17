"""
作业线收件箱模型 (Workline Inbox)

统一编排入口的持久化载体，接收所有外部事件和消息。
遵循白皮书 8.7 节规范。

相关文档:
- 白皮书: @docs/workline_plugin_architecture_design.md
"""

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Column, Text
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field, Relationship

from src.core.mixins import BaseMixin, DataTableMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType

if TYPE_CHECKING:
    from src.app.workline.models.session import WorklineSession


# ==================== 枚举定义 ====================


class InboxKind(str, Enum):
    """收件箱消息类型枚举 (白皮书 8.7)"""

    # 设备事件
    DEVICE_EVENT = "DEVICE_EVENT"  # 设备上报的事件

    # 外部系统调用
    EXTERNAL_HTTP = "EXTERNAL_HTTP"  # HTTP 回调

    # 定时器
    TIMER_TIMEOUT = "TIMER_TIMEOUT"  # 定时器超时

    # 人工操作
    MANUAL_HOLD = "MANUAL_HOLD"  # 人工暂停
    MANUAL_RESUME = "MANUAL_RESUME"  # 人工恢复
    MANUAL_CANCEL = "MANUAL_CANCEL"  # 人工取消

    # 重放请求
    REPLAY_REQUEST = "REPLAY_REQUEST"  # 重放请求


class InboxStatus(str, Enum):
    """收件箱消息状态枚举 (白皮书 8.7)"""

    NEW = "NEW"  # 新消息
    PROCESSING = "PROCESSING"  # 处理中
    PROCESSED = "PROCESSED"  # 已处理
    FAILED = "FAILED"  # 处理失败


class SourceSystem(str, Enum):
    """来源系统枚举"""

    DEVICE = "DEVICE"  # 设备
    WCS = "WCS"  # 仓储控制系统
    MES = "MES"  # 制造执行系统
    ERP = "ERP"  # 企业资源计划
    MANUAL = "MANUAL"  # 人工操作
    SYSTEM = "SYSTEM"  # 系统内部（定时器等）


# ==================== 基础字段 (用于 Schema 复用) ====================


class WorklineInboxBase(BaseMixin):
    """收件箱基础字段 - 用于 Schema 复用"""

    # 消息标识
    kind: InboxKind = Field(
        index=True,
        sa_type=SQLAEnum(
            InboxKind,
            native_enum=False,
            create_constraint=True,
            length=50,
        ),
        description="消息类型",
    )

    idempotency_key: str | None = Field(
        default=None,
        max_length=200,
        index=True,
        description="幂等键（防止重复处理）",
    )

    # 来源信息
    source_system: SourceSystem = Field(
        index=True,
        sa_type=SQLAEnum(
            SourceSystem,
            native_enum=False,
            create_constraint=True,
            length=50,
        ),
        description="来源系统",
    )

    source_message_id: str | None = Field(
        default=None,
        max_length=200,
        description="来源系统消息 ID",
    )

    # 关联信息
    workline_id: int | None = Field(
        default=None,
        index=True,
        foreign_key="wes_biz.work_lines.id",
        description="作业线 ID（关联 WorkLine.id）",
    )

    device_id: int | None = Field(
        default=None,
        index=True,
        foreign_key="wes_biz.devices.id",
        description="设备 ID（关联 Device.id）",
    )

    command_id: int | None = Field(
        default=None,
        index=True,
        foreign_key="wes_biz.device_commands.id",
        description="设备指令 ID（关联 DeviceCommand.id）",
    )

    session_id: int | None = Field(
        default=None,
        index=True,
        foreign_key="wes_biz.workline_sessions.id",
        description="会话 ID（关联 WorklineSession.id）",
    )

    correlation_id: str | None = Field(
        default=None,
        max_length=100,
        index=True,
        description="关联 ID（串联业务流程）",
    )

    # 消息内容
    payload_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON),
        description="消息负载（JSON 格式）",
    )

    # 处理状态
    status: InboxStatus = Field(
        default=InboxStatus.NEW,
        index=True,
        sa_type=SQLAEnum(
            InboxStatus,
            native_enum=False,
            create_constraint=True,
            length=50,
        ),
        description="处理状态",
    )

    processor_token: str | None = Field(
        default=None,
        max_length=200,
        description="处理器令牌（用于锁定）",
    )

    received_at: datetime = Field(
        default_factory=datetime.now,
        index=True,
        description="接收时间",
    )

    processed_at: datetime | None = Field(
        default=None,
        description="处理完成时间",
    )

    error_message: str | None = Field(
        default=None,
        sa_column=Column(Text),
        description="错误消息",
    )


# ==================== 数据库表模型 ====================


class WorklineInbox(
    WorklineInboxBase,
    DataTableMixin,
    table=True,
):
    """
    作业线收件箱数据库表模型

    统一编排入口的持久化载体，接收所有外部事件和消息。

    字段说明:
    - kind: 消息类型（设备事件、外部调用、定时器等）
    - source_system: 来源系统
    - idempotency_key: 幂等键（防止重复处理）
    - session_id/workline_id/device_id/command_id: 关联对象
    - payload_json: 消息负载
    - processor_token: 处理器令牌（用于锁定）

    状态机:
        NEW → PROCESSING → PROCESSED
               ↓
             FAILED
    """

    __tablename__: str = "workline_inbox"
    __schema__ = SchemaType.BIZ.value  # 业务数据表

    # 关系定义
    session: "WorklineSession" = Relationship(
        back_populates=None,
        sa_relationship_kwargs={
            "lazy": "selectin",
            "foreign_keys": "WorklineInbox.session_id",
            "primaryjoin": "WorklineInbox.session_id == WorklineSession.id",
        },
    )


# ==================== 自动生成的 Schema ====================


class WorklineInboxCreate(ModelFactory(WorklineInboxBase).for_create()):
    """收件箱创建 Schema"""


# ==================== 导出 ====================


__all__ = [
    "InboxKind",
    "InboxStatus",
    "SourceSystem",
    "WorklineInbox",
    "WorklineInboxBase",
    "WorklineInboxCreate",
]
