"""
作业线时间线模型 (Workline Timeline)

用于记录会话执行的详细时间线，是排障主视图。

相关文档:
- 目标架构:
  @docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md

本模型属于收敛前 implementation_baseline。
"""

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar, cast

from sqlalchemy import JSON, Column, Text, UniqueConstraint
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field, Relationship

from src.core.mixins import BaseMixin, DataTableMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType

if TYPE_CHECKING:
    from src.app.runtime.orchestration.models.session import WorklineSession


# ==================== 枚举定义 ====================


class TimelineStage(str, Enum):
    """收敛前 implementation_baseline 的时间线阶段枚举。"""

    INGEST = "INGEST"  # 接收
    ROUTE = "ROUTE"  # 路由
    DECISION = "DECISION"  # 决策
    DISPATCH_PREPARE = "DISPATCH_PREPARE"  # 派发准备
    WAITING = "WAITING"  # 等待
    CALLBACK = "CALLBACK"  # 回调
    MANUAL = "MANUAL"  # 人工干预
    TIMEOUT = "TIMEOUT"  # 超时
    COMPENSATION = "COMPENSATION"  # 补偿
    COMPLETE = "COMPLETE"  # 完成
    FAIL = "FAIL"  # 失败


class TimelineActionType(str, Enum):
    """时间线动作类型枚举"""

    # 会话管理
    SESSION_CREATED = "SESSION_CREATED"
    SESSION_STARTED = "SESSION_STARTED"
    SESSION_RESUMED = "SESSION_RESUMED"
    SESSION_COMPLETED = "SESSION_COMPLETED"
    SESSION_FAILED = "SESSION_FAILED"
    SESSION_CANCELLED = "SESSION_CANCELLED"

    # 状态转换
    STATUS_CHANGED = "STATUS_CHANGED"

    # 等待管理
    WAIT_STARTED = "WAIT_STARTED"
    WAIT_RESUMED = "WAIT_RESUMED"
    WAIT_TIMEOUT = "WAIT_TIMEOUT"

    # 设备交互
    COMMAND_SENT = "COMMAND_SENT"
    COMMAND_ACKED = "COMMAND_ACKED"
    COMMAND_COMPLETED = "COMMAND_COMPLETED"
    COMMAND_FAILED = "COMMAND_FAILED"

    # 事件处理
    EVENT_RECEIVED = "EVENT_RECEIVED"
    EVENT_PROCESSED = "EVENT_PROCESSED"
    EVENT_FAILED = "EVENT_FAILED"

    # 外部调用
    EXTERNAL_CALL_STARTED = "EXTERNAL_CALL_STARTED"
    EXTERNAL_CALL_COMPLETED = "EXTERNAL_CALL_COMPLETED"
    EXTERNAL_CALL_FAILED = "EXTERNAL_CALL_FAILED"

    # 决策记录
    DECISION_MADE = "DECISION_MADE"

    # 错误和补偿
    ERROR_OCCURRED = "ERROR_OCCURRED"
    COMPENSATION_STARTED = "COMPENSATION_STARTED"
    COMPENSATION_COMPLETED = "COMPENSATION_COMPLETED"

    # 人工操作
    MANUAL_HOLD = "MANUAL_HOLD"
    MANUAL_RESUME = "MANUAL_RESUME"


class TimelineActorType(str, Enum):
    """时间线参与者类型枚举"""

    PLUGIN = "PLUGIN"  # 插件
    DEVICE = "DEVICE"  # 设备
    EXTERNAL_SYSTEM = "EXTERNAL_SYSTEM"  # 外部系统
    ORCHESTRATOR = "ORCHESTRATOR"  # 编排器
    MANUAL_OPERATOR = "MANUAL_OPERATOR"  # 人工操作员


class TimelineStatus(str, Enum):
    """时间线条目状态枚举"""

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    PENDING = "PENDING"


# ==================== 基础字段 (用于 Schema 复用) ====================


class WorklineTimelineBase(BaseMixin):
    """时间线基础字段 - 用于 Schema 复用"""

    session_id: int = Field(
        index=True,
        foreign_key="wes_biz.workline_sessions.id",
        description="会话 ID（关联 WorklineSession.id）",
    )

    workline_id: int = Field(
        index=True,
        foreign_key="wes_biz.work_lines.id",
        description="作业线 ID（关联 WorkLine.id）",
    )

    trace_id: str | None = Field(
        default=None,
        max_length=100,
        index=True,
        description="Trace ID（串联业务流程）",
    )

    # 序列号（同一 session_id 内单调递增）
    seq_no: int = Field(
        index=True,
        description="序列号（同一会话内递增）",
    )

    occurred_at: datetime = Field(
        index=True,
        description="发生时间",
    )

    # 🔥 使用 VARCHAR + CHECK 约束
    stage: TimelineStage = Field(
        index=True,
        sa_type=cast(
            "Any",
            SQLAEnum(
                TimelineStage,
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
        ),
        description="阶段",
    )

    # 🔥 使用 VARCHAR + CHECK 约束
    action_type: TimelineActionType = Field(
        index=True,
        sa_type=cast(
            "Any",
            SQLAEnum(
                TimelineActionType,
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
        ),
        description="动作类型",
    )

    # 🔥 使用 VARCHAR + CHECK 约束
    actor_type: TimelineActorType = Field(
        sa_type=cast(
            "Any",
            SQLAEnum(
                TimelineActorType,
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
        ),
        description="参与者类型",
    )

    actor_code: str | None = Field(
        default=None,
        max_length=100,
        description="参与者编码（如设备编码）",
    )

    from_status: str | None = Field(
        default=None,
        max_length=50,
        description="原状态",
    )

    to_status: str | None = Field(
        default=None,
        max_length=50,
        description="目标状态",
    )

    # 🔥 使用 VARCHAR + CHECK 约束
    status: TimelineStatus = Field(
        default=TimelineStatus.SUCCESS,
        index=True,
        sa_type=cast(
            "Any",
            SQLAEnum(
                TimelineStatus,
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
        ),
        description="条目状态",
    )

    failure_domain: str | None = Field(
        default=None,
        max_length=100,
        description="失败域",
    )

    message: str | None = Field(
        default=None,
        sa_column=Column(Text),
        description="消息",
    )

    payload_json: dict[str, object] | None = Field(
        default=None,
        sa_column=Column(JSON),
        description="负载数据（JSON 格式）",
    )

    # 关联对象（便于追溯，部分不设置外键以避免循环依赖）
    related_inbox_id: int | None = Field(
        default=None,
        description="关联的 Inbox ID（不设外键，避免循环依赖）",
    )

    related_command_id: int | None = Field(
        default=None,
        foreign_key="wes_biz.device_commands.id",
        description="关联的设备指令 ID",
    )


# ==================== 数据库表模型 ====================


class WorklineTimeline(
    WorklineTimelineBase,
    DataTableMixin,
    table=True,
):
    """
    作业线时间线数据库表模型

    记录会话执行的详细时间线，是排障主视图。

    字段说明:
    - session_id: 关联的会话
    - seq_no: 序列号（同一会话内单调递增）
    - stage/action_type: 阶段和动作类型
    - actor_type/actor_code: 参与者信息
    - from_status/to_status: 状态转换
    - related_inbox_id/related_command_id: 关联对象（便于追溯）

    约束:
    - seq_no 必须在同一 session_id 内单调递增
    - Timeline 由编排层统一生成，插件不直接构造
    """

    __tablename__: ClassVar[str] = "workline_timelines"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value  # 业务数据表
    __table_args__ = (
        UniqueConstraint("session_id", "seq_no", name="uq_workline_timelines_session_seq_no"),
        {"schema": SchemaType.BIZ.value},
    )

    # 关系定义
    session: "WorklineSession" = Relationship(
        back_populates=None,
        sa_relationship_kwargs={
            "lazy": "selectin",
            "foreign_keys": "WorklineTimeline.session_id",
            "primaryjoin": "WorklineTimeline.session_id == WorklineSession.id",
        },
    )


# ==================== 自动生成的 Schema ====================


class WorklineTimelineCreate(ModelFactory(WorklineTimelineBase).for_create()):
    """时间线创建 Schema"""


# ==================== 导出 ====================


__all__ = [
    "TimelineActionType",
    "TimelineActorType",
    "TimelineStage",
    "TimelineStatus",
    "WorklineTimeline",
    "WorklineTimelineBase",
    "WorklineTimelineCreate",
]
