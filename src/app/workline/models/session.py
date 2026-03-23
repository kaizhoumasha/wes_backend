"""
作业线会话模型 (Workline Session)

用于跟踪一次完整的业务链路执行过程，管理会话状态和上下文。
遵循白皮书 8.3 节规范。

相关文档:
- 白皮书: @docs/workline_plugin_architecture_design.md
"""

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar, cast

from sqlalchemy import JSON, Column, Text
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field, Relationship

from src.core.mixins import BaseMixin, DataTableMixin, SoftDeleteMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType

if TYPE_CHECKING:
    from src.app.workline.models.workline import WorkLine


# ==================== 枚举定义 ====================


class SessionStatus(str, Enum):
    """会话状态枚举 (白皮书 8.3)"""

    NEW = "NEW"  # 新建
    RUNNING = "RUNNING"  # 运行中
    WAITING_DEVICE_RESULT = "WAITING_DEVICE_RESULT"  # 等待设备响应
    WAITING_EXTERNAL = "WAITING_EXTERNAL"  # 等待外部系统
    MANUAL_HOLD = "MANUAL_HOLD"  # 人工暂停
    COMPLETED = "COMPLETED"  # 已完成
    FAILED = "FAILED"  # 失败
    CANCELLED = "CANCELLED"  # 已取消


class RunMode(str, Enum):
    """运行模式枚举"""

    AUTO = "AUTO"  # 自动模式
    MANUAL = "MANUAL"  # 手动模式
    SIMULATION = "SIMULATION"  # 模拟模式
    REPLAY = "REPLAY"  # 重放模式


# ==================== 基础字段 (用于 Schema 复用) ====================


class WorklineSessionBase(BaseMixin):
    """会话基础字段 - 用于 Schema 复用"""

    session_code: str = Field(
        max_length=100,
        unique=True,
        index=True,
        description="会话编码（业务主键）",
    )

    workline_id: int = Field(
        index=True,
        foreign_key="wes_biz.work_lines.id",
        description="作业线 ID（关联 WorkLine.id）",
    )

    plugin_key: str = Field(
        max_length=100,
        index=True,
        description="插件标识（如 packing_zone, smt）",
    )

    # 🔥 使用 VARCHAR + CHECK 约束
    run_mode: RunMode = Field(
        default=RunMode.AUTO,
        sa_type=cast(
            "Any",
            SQLAEnum(
                RunMode,
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
        ),
        description="运行模式",
    )

    business_key: str | None = Field(
        default=None,
        max_length=100,
        index=True,
        description="业务键（如订单号、托盘码）",
    )

    barcode: str | None = Field(
        default=None,
        max_length=100,
        index=True,
        description="条码（物料标识）",
    )

    # 🔥 使用 VARCHAR + CHECK 约束
    status: SessionStatus = Field(
        default=SessionStatus.NEW,
        index=True,
        sa_type=cast(
            "Any",
            SQLAEnum(
                SessionStatus,
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
        ),
        description="会话状态",
    )

    context_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSON),
        description="会话上下文（JSON 格式）",
    )

    context_schema_version: str | None = Field(
        default=None,
        max_length=50,
        description="上下文 Schema 版本（插件管理）",
    )

    started_at: datetime | None = Field(
        default=None,
        index=True,
        description="会话开始时间",
    )

    ended_at: datetime | None = Field(
        default=None,
        description="会话结束时间",
    )

    correlation_id: str | None = Field(
        default=None,
        max_length=100,
        index=True,
        description="关联 ID（串联整个业务流程）",
    )

    # 等待状态相关字段
    current_wait_type: str | None = Field(
        default=None,
        max_length=100,
        description="当前等待类型（如 DEVICE_CALLBACK, EXTERNAL_API）",
    )

    current_wait_token: str | None = Field(
        default=None,
        max_length=200,
        description="当前等待令牌（用于回调匹配）",
    )

    waiting_since: datetime | None = Field(
        default=None,
        description="开始等待时间",
    )

    deadline_at: datetime | None = Field(
        default=None,
        description="超时截止时间",
    )

    awaiting_command_id: int | None = Field(
        default=None,
        index=True,
        foreign_key="wes_biz.device_commands.id",
        description="等待的设备指令 ID",
    )

    # 失败相关字段
    failure_domain: str | None = Field(
        default=None,
        max_length=100,
        description="失败域（如 DEVICE, PLUGIN, EXTERNAL）",
    )

    failure_code: str | None = Field(
        default=None,
        max_length=50,
        description="失败码",
    )

    failure_message: str | None = Field(
        default=None,
        sa_column=Column(Text),
        description="失败消息",
    )

    retry_count: int = Field(
        default=0,
        ge=0,
        description="重试次数",
    )

    # 追溯辅助字段（不设置外键，避免循环依赖）
    last_inbox_id: int | None = Field(
        default=None,
        description="最后处理的 Inbox ID（便于重放）",
    )

    last_decision_id: int | None = Field(
        default=None,
        description="最后一次决策 ID（便于排障和回溯）",
    )


# ==================== 数据库表模型 ====================


class WorklineSession(
    WorklineSessionBase,
    DataTableMixin,
    SoftDeleteMixin,
    table=True,
):
    """
    作业线会话数据库表模型

    跟踪一次完整业务链路的执行过程，管理会话状态和上下文。

    字段说明:
    - session_code: 会话唯一标识
    - workline_id: 关联的作业线
    - plugin_key: 执行的插件标识
    - status: 会话状态（由插件状态机管理）
    - context_json: 插件自定义上下文
    - awaiting_command_id: 当前等待的设备指令
    - last_inbox_id/last_decision_id: 追溯辅助字段

    状态机:
        NEW → RUNNING → WAITING_* → COMPLETED
               ↓         ↓
             FAILED   CANCELLED
    """

    __tablename__: ClassVar[str] = "workline_sessions"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value  # 业务数据表

    # 关系定义
    workline: "WorkLine" = Relationship(
        back_populates=None,
        sa_relationship_kwargs={
            "lazy": "selectin",
            "foreign_keys": "WorklineSession.workline_id",
            "primaryjoin": "WorklineSession.workline_id == WorkLine.id",
        },
    )


# ==================== 自动生成的 Schema ====================


class WorklineSessionCreate(ModelFactory(WorklineSessionBase).for_create()):
    """会话创建 Schema"""


class WorklineSessionUpdate(ModelFactory(WorklineSessionBase).for_update()):
    """会话更新 Schema"""


# ==================== 导出 ====================


__all__ = [
    "RunMode",
    "SessionStatus",
    "WorklineSession",
    "WorklineSessionBase",
    "WorklineSessionCreate",
    "WorklineSessionUpdate",
]
