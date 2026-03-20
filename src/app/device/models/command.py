"""
设备指令模型 (Device Command)

用于管理与设备交互的指令生命周期，遵循白皮书规范。
支持 SDAF 控制循环：Sense -> Decide -> Act -> Feedback

相关文档:
- 白皮书: @docs/third_party_integration_whitepaper.md
- 现有 Device: device.py
"""

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar, cast

from pydantic import field_validator
from sqlalchemy import JSON, Column
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field, Relationship

from src.core.mixins import BaseMixin, DataTableMixin, EnterpriseMixin, SoftDeleteMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from src.app.device.models.device import Device


# ==================== 枚举定义 ====================


class TaskType(str, Enum):
    """任务类型枚举 (白皮书 5.1)"""

    PICK = "PICK"  # 抓取/取货
    PUT = "PUT"  # 放置/卸货
    SCAN = "SCAN"  # 扫码/识别
    ROTATE = "ROTATE"  # 旋转
    PROCESS = "PROCESS"  # 加工/检测
    # 组合任务
    PICK_AND_PLACE = "PICK_AND_PLACE"  # 抓取并放置


class CommandStatus(str, Enum):
    """指令状态枚举

    状态机流转:
    PENDING -> SENT -> ACK_RECEIVED -> COMPLETED
       ↓         ↓          ↓
    CANCELLED  TIMEOUT    FAILED
    """

    PENDING = "PENDING"  # 待发送
    SENT = "SENT"  # 已发送（等待 ACK）
    ACK_RECEIVED = "ACK_RECEIVED"  # 已接收确认
    COMPLETED = "COMPLETED"  # 已完成
    FAILED = "FAILED"  # 失败
    TIMEOUT = "TIMEOUT"  # 超时
    CANCELLED = "CANCELLED"  # 已取消


class CommandResult(str, Enum):
    """指令执行结果枚举"""

    SUCCESS = "SUCCESS"  # 成功
    FAILED = "FAILED"  # 失败


# ==================== 基础字段 (用于 Schema 复用) ====================


class CommandBase(BaseMixin):
    """指令基础字段 - 用于 Schema 复用"""

    device_id: int = Field(
        index=True,
        foreign_key="wes_biz.devices.id",
        description="目标设备 ID（关联 Device.id）",
    )

    # 🔥 使用 VARCHAR + CHECK 约束
    task_type: TaskType = Field(
        sa_type=cast("Any", SQLAEnum(
            TaskType,
            native_enum=False,
            create_constraint=True,
            length=50,
        )),
        description="任务类型",
    )

    priority: int = Field(
        default=5,
        ge=1,
        le=10,
        description="优先级（1-10，10 最高）",
    )
    timeout_ms: int = Field(
        default=30000,
        ge=1000,
        le=300000,
        description="超时时间（毫秒）",
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON),
        description="业务参数（JSON 格式）",
    )

    @field_validator("params", mode="before")
    @classmethod
    def validate_params(cls, v: Any) -> dict[str, Any]:
        """确保 params 是字典"""
        if v is None:
            return {}
        if isinstance(v, dict):
            return cast("dict[str, Any]", v)
        raise ValueError("params 必须是字典类型")


# ==================== Pydantic Schema ====================


class CommandRequest(CommandBase):
    """指令请求 Schema - 用于创建指令"""

    command_code: str | None = Field(
        default=None,
        max_length=100,
        description="全局唯一指令编码，为空时自动生成",
    )
    correlation_id: str | None = Field(
        default=None,
        max_length=100,
        description="关联 ID（串联整个流程）",
    )


class CommandResponse(CommandBase):
    """指令响应 Schema - 返回给客户端"""

    command_code: str
    status: CommandStatus
    result: CommandResult | None
    sent_at: datetime | None
    ack_received_at: datetime | None
    completed_at: datetime | None
    result_data: dict[str, Any] | None
    error_detail: dict[str, Any] | None
    retry_count: int
    correlation_id: str | None
    created_at: datetime
    updated_at: datetime


class CommandCallbackResult(BaseMixin):
    """指令回调结果 Schema - 设备回调时使用"""

    command_code: str = Field(description="指令编码（必须与原指令一致）")
    device_code: str = Field(description="设备编码（device_code，设备标识）")
    result: CommandResult = Field(description="执行结果")
    finish_time: int = Field(description="完成时间（Unix 时间戳，毫秒）")
    data: dict[str, Any] | None = Field(default=None, description="业务回传数据")
    error_detail: dict[str, Any] | None = Field(
        default=None,
        description="错误详情（result=FAILED 时必填）",
    )


class CommandAck(BaseMixin):
    """指令确认响应 - 设备返回的 ACK"""

    code: int = Field(description="响应码（200: 成功，400: 参数错误，503: 设备忙）")
    message: str = Field(description="响应消息")
    trace_id: str | None = Field(default=None, description="设备内部日志 ID")


# ==================== 数据库表模型 ====================


class DeviceCommand(
    CommandBase,
    EnterpriseMixin,
    SoftDeleteMixin,
    DataTableMixin,
    table=True,
):
    """
    设备指令数据库表模型

    继承 CommandBase 提供基础指令字段:
    - device_id
    - task_type
    - priority
    - timeout_ms
    - params

    当前模型补充指令生命周期相关字段:
    - command_code
    - correlation_id
    - status
    - sent_at / ack_received_at / completed_at
    - result / result_data / error_detail
    - retry_count

    状态机:
        PENDING -> SENT -> ACK_RECEIVED -> COMPLETED
           ↓         ↓          ↓
        CANCELLED  TIMEOUT    FAILED
    """

    __tablename__: ClassVar[str] = "device_commands"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value  # 业务数据表

    # 业务唯一编码（对外可见）
    command_code: str = Field(
        max_length=100,
        unique=True,
        index=True,
        description="全局唯一指令编码",
    )

    # 关联 ID（串联整个流程）
    correlation_id: str | None = Field(
        default=None,
        max_length=100,
        index=True,
        description="关联 ID（串联整个流程）",
    )

    # 会话 ID（跟踪单个任务会话）
    session_id: str | None = Field(
        default=None,
        max_length=100,
        index=True,
        description="会话 ID（跟踪单个任务会话）",
    )

    # 作业线 ID（关联到 WorkLine）
    workline_id: int | None = Field(
        default=None,
        index=True,
        foreign_key="wes_biz.work_lines.id",
        description="作业线 ID（关联 WorkLine.id）",
    )

    # 🔥 状态字段：使用 VARCHAR + CHECK 约束
    status: CommandStatus = Field(
        default=CommandStatus.PENDING,
        index=True,
        sa_type=cast("Any", SQLAEnum(
            CommandStatus,
            native_enum=False,
            create_constraint=True,
            length=50,
        )),
        description="指令状态",
    )

    # 时间戳（发送、ACK、完成）
    sent_at: datetime | None = Field(
        default=None,
        description="指令发送时间",
    )
    ack_received_at: datetime | None = Field(
        default=None,
        description="ACK 接收时间",
    )
    completed_at: datetime | None = Field(
        default=None,
        description="指令完成时间",
    )

    # 🔥 执行结果：使用 VARCHAR + CHECK 约束
    result: CommandResult | None = Field(
        default=None,
        sa_type=cast("Any", SQLAEnum(
            CommandResult,
            native_enum=False,
            create_constraint=True,
            length=50,
        )),
        description="执行结果（SUCCESS/FAILED）",
    )
    result_data: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSON),
        description="结果数据（JSON 格式）",
    )

    # 错误详情
    error_detail: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSON),
        description="错误详情（JSON 格式）",
    )

    # 重试次数
    retry_count: int = Field(
        default=0,
        ge=0,
        description="重试次数",
    )

    # ACK 响应（设备返回的确认）
    ack_code: int | None = Field(
        default=None,
        description="ACK 响应码",
    )
    ack_message: str | None = Field(
        default=None,
        max_length=500,
        description="ACK 响应消息",
    )
    ack_trace_id: str | None = Field(
        default=None,
        max_length=100,
        description="ACK 设备内部日志 ID",
    )

    # 关系定义
    device: "Device" = Relationship(
        back_populates=None,
        sa_relationship_kwargs={
            "lazy": "selectin",
            "foreign_keys": "DeviceCommand.device_id",
            "primaryjoin": "DeviceCommand.device_id == Device.id",
        },
    )

    def can_retry(self) -> bool:
        """检查是否可以重试"""
        return self.status in [CommandStatus.FAILED, CommandStatus.TIMEOUT] and self.retry_count < 3

    def is_timeout(self) -> bool:
        """检查是否超时"""
        if self.sent_at is None:
            return False
        elapsed_ms = int((timezone.now_for_db() - self.sent_at).total_seconds() * 1000)
        return elapsed_ms > self.timeout_ms

    def get_duration_ms(self) -> int | None:
        """获取指令执行时长（毫秒）"""
        if self.sent_at and self.completed_at:
            return int((self.completed_at - self.sent_at).total_seconds() * 1000)
        return None


# ==================== 自动生成的 Schema ====================


class DeviceCommandCreate(ModelFactory(CommandBase).for_create()):
    """设备指令创建 Schema"""

    command_code: str | None = None
    correlation_id: str | None = None


class DeviceCommandUpdate(ModelFactory(CommandBase).for_optimistic_update()):
    """设备指令更新 Schema"""


# ==================== 导出 ====================


__all__ = [
    "CommandAck",
    "CommandBase",
    "CommandCallbackResult",
    "CommandRequest",
    "CommandResponse",
    "CommandResult",
    "CommandStatus",
    "DeviceCommand",
    "DeviceCommandCreate",
    "DeviceCommandUpdate",
    "TaskType",
]
