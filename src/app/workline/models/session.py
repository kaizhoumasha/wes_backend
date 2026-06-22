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

from sqlalchemy import JSON, Column, ForeignKey, Index, Text, text
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field, Relationship

from src.core.mixins import BaseMixin, DataTableMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
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


class RuntimeReconciliationState(str, Enum):
    """运行时对账状态。"""

    PENDING = "PENDING"
    RESOLVED = "RESOLVED"


class RuntimeReconciliationReason(str, Enum):
    """运行时对账原因。"""

    CALLBACK_DEADLINE_EXPIRED = "CALLBACK_DEADLINE_EXPIRED"
    COMMAND_ACK_EXHAUSTED = "COMMAND_ACK_EXHAUSTED"
    OUTBOX_DISPATCH_FAILED = "OUTBOX_DISPATCH_FAILED"


class RuntimeReconciliationSourceKind(str, Enum):
    """运行时对账来源类型。"""

    TIMER_TIMEOUT = "TIMER_TIMEOUT"
    DISPATCH_ACK_EXHAUSTED = "DISPATCH_ACK_EXHAUSTED"


class RuntimeReconciliationResolution(str, Enum):
    """运行时对账人工决议。"""

    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


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
    contract_version: str | None = Field(
        default=None,
        max_length=50,
        description="执行时绑定的协议版本",
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

    trace_id: str | None = Field(
        default=None,
        max_length=100,
        index=True,
        description="统一 Trace ID（串联整个业务流程）",
    )

    # 等待状态相关字段
    current_wait_type: str | None = Field(
        default=None,
        max_length=100,
        description="当前等待类型（如 DEVICE_CALLBACK, EXTERNAL_API）",
    )

    waiting_since: datetime | None = Field(
        default=None,
        description="开始等待时间",
    )

    deadline_at: datetime | None = Field(
        default=None,
        description="超时截止时间",
    )

    current_wait_timeout_seconds: int | None = Field(
        default=None,
        ge=1,
        description="当前等待声明的业务完成窗口秒数；COMMAND_RESULT 等待在 ACK 后据此激活 deadline_at",
    )

    awaiting_command_id: int | None = Field(
        default=None,
        index=True,
        # 标记与 DeviceCommand.session_id_int 的已知外键环，
        # 避免测试库 drop_all 清理排序 warning。
        sa_column_args=(
            ForeignKey(
                "wes_biz.device_commands.id",
                name="fk_workline_sessions_awaiting_command_id_device_commands",
                use_alter=True,
            ),
        ),
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

    ingress_count: int = Field(
        default=1,
        ge=1,
        description="入口命中次数（同一 DEVICE_EVENT 命中并复用会话时递增）",
    )

    last_request_id: str | None = Field(
        default=None,
        max_length=200,
        description="最近一次入口请求 ID（对齐 callback_logs.request_id / inbox.source_message_id）",
    )

    last_ingress_at: datetime | None = Field(
        default=None,
        description="最近一次入口命中时间",
    )

    # 追溯辅助字段（不设置外键，避免循环依赖）
    last_inbox_id: int | None = Field(
        default=None,
        description="最后处理的 Inbox ID（便于重放）",
    )
    current_material_unit_id: int | None = Field(
        default=None,
        sa_type=SQL_COMPAT_BIGINT,
        description="当前在途料盘 ID（引用 material_units.id，无外键遵循辅助追溯字段规范）",
    )

    # runtime reconciliation 一等字段；guard/CAS/resolve 只读取这些字段，
    # 不从 context_json 推断控制状态。
    reconciliation_state: RuntimeReconciliationState | None = Field(
        default=None,
        index=True,
        sa_type=cast(
            "Any",
            SQLAEnum(
                RuntimeReconciliationState,
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
        ),
        description="运行时对账状态",
    )
    reconciliation_reason: RuntimeReconciliationReason | None = Field(
        default=None,
        index=True,
        sa_type=cast(
            "Any",
            SQLAEnum(
                RuntimeReconciliationReason,
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
        ),
        description="运行时对账原因",
    )
    reconciliation_source_kind: RuntimeReconciliationSourceKind | None = Field(
        default=None,
        max_length=50,
        sa_type=cast(
            "Any",
            SQLAEnum(
                RuntimeReconciliationSourceKind,
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
        ),
        description="运行时对账来源类型",
    )
    reconciliation_source_inbox_id: int | None = Field(default=None, index=True, description="触发对账的 Inbox ID")
    reconciliation_source_outbox_id: int | None = Field(default=None, index=True, description="触发对账的 Outbox ID")
    reconciliation_command_id: int | None = Field(default=None, index=True, description="触发对账的设备指令 ID")
    reconciliation_device_id: int | None = Field(default=None, index=True, description="触发对账的设备 ID")
    reconciliation_wait_token: str | None = Field(default=None, max_length=200, description="触发对账的等待令牌")
    reconciliation_ack_received_at: datetime | None = Field(default=None, description="触发对账时的 ACK 接收时间")
    reconciliation_deadline_at: datetime | None = Field(default=None, description="触发对账时的执行等待截止时间")
    reconciliation_occurred_at: datetime | None = Field(default=None, index=True, description="运行时对账发生时间")
    reconciliation_late_evidence_received: bool = Field(default=False, description="是否已收到迟到 callback 证据")
    reconciliation_resolution: RuntimeReconciliationResolution | None = Field(
        default=None,
        max_length=50,
        sa_type=cast(
            "Any",
            SQLAEnum(
                RuntimeReconciliationResolution,
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
        ),
        description="运行时对账人工决议",
    )
    reconciliation_resolved_at: datetime | None = Field(default=None, index=True, description="运行时对账解除时间")


# ==================== 数据库表模型 ====================


class WorklineSession(
    WorklineSessionBase,
    DataTableMixin,
    table=True,
):
    """
    作业线会话数据库表模型

    跟踪一次完整业务链路的执行过程，承载 Runtime-owned Session lifecycle 和上下文。

    字段说明:
    - session_code: 会话唯一标识
    - workline_id: 关联的作业线
    - plugin_key: 执行的插件标识
    - status: 会话状态（由 Runtime 根据 RuntimeIntent 和外部事实推进）
    - context_json: Runtime 与插件共享的业务上下文快照
    - awaiting_command_id: 当前等待的设备指令
    - last_inbox_id: 追溯辅助字段

    Runtime lifecycle:
        NEW → RUNNING → WAITING_* → COMPLETED
               ↓         ↓
             FAILED   CANCELLED
    """

    __tablename__: ClassVar[str] = "workline_sessions"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value  # 业务数据表
    __table_args__ = (
        Index(
            "uq_workline_sessions_open_business_key",
            "workline_id",
            "business_key",
            unique=True,
            postgresql_where=text(
                "business_key IS NOT NULL AND status IN "
                "('NEW', 'RUNNING', 'WAITING_DEVICE_RESULT', 'WAITING_EXTERNAL', 'MANUAL_HOLD')"
            ),
            sqlite_where=text(
                "business_key IS NOT NULL AND status IN "
                "('NEW', 'RUNNING', 'WAITING_DEVICE_RESULT', 'WAITING_EXTERNAL', 'MANUAL_HOLD')"
            ),
        ),
        Index("ix_workline_sessions_current_material_unit_id", "current_material_unit_id"),
        {"schema": SchemaType.BIZ.value},
    )

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
    "RuntimeReconciliationReason",
    "RuntimeReconciliationResolution",
    "RuntimeReconciliationSourceKind",
    "RuntimeReconciliationState",
    "SessionStatus",
    "WorklineSession",
    "WorklineSessionBase",
    "WorklineSessionCreate",
    "WorklineSessionUpdate",
]
