"""
第三方设备接入模型

用于管理接入 P9 WES 的第三方自动化设备（机械臂、输送线等）
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import JSON, Column, ForeignKey, String, Text
from sqlalchemy.orm import relationship
from sqlmodel import Field

from src.core.mixins import DataTableMixin, SoftDeleteMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType

# ============================================================================
# 基础字段模型（用于 Schema 复用）
# ============================================================================


class DeviceBase(BaseModel):
    """设备基础字段 - 用于 Schema 复用"""

    device_id: str
    device_name: str
    device_type: str
    ip_address: str
    port: int
    protocol: str = "HTTP"


class DeviceCommandBase(BaseModel):
    """指令基础字段 - 用于 Schema 复用"""

    command_id: str
    device_id: str
    task_type: str
    priority: int = 1
    timeout_ms: int = 30000
    # params 在表模型中定义


class DeviceEventBase(BaseModel):
    """设备事件基础字段 - 用于 Schema 复用"""

    device_id: str
    event_type: str
    # event_data 在表模型中定义


# ============================================================================
# 数据库表模型
# ============================================================================


class Device(DeviceBase, DataTableMixin, SoftDeleteMixin, table=True):
    """设备注册表

    记录接入 WES 的第三方设备信息，用于指令下发和状态管理
    """

    __tablename__ = "devices"
    __schema__ = SchemaType.SYS.value
    __table_args__ = {"comment": "第三方设备注册表"}

    # 重新定义基础字段（使用 SQLModel Field）
    device_id: str = Field(max_length=50, unique=True, nullable=False, description="设备唯一标识")
    device_name: str = Field(max_length=100, nullable=False, description="设备名称")
    device_type: str = Field(max_length=50, nullable=False, description="设备类型")
    ip_address: str = Field(max_length=50, nullable=False, description="设备 IP 地址")
    port: int = Field(nullable=False, description="设备 API 端口")
    protocol: str = Field(default="HTTP", max_length=10, description="通信协议")

    # 扩展字段
    auth_token: str | None = Field(default=None, sa_column=Column(Text), description="认证 Token")
    is_online: bool = Field(default=False, description="是否在线")
    last_heartbeat: datetime | None = Field(default=None, description="最后心跳时间")
    status: str = Field(default="IDLE", max_length=20, description="设备状态")
    current_command_id: str | None = Field(default=None, sa_column=Column(String(100)), description="当前执行的指令 ID")

    # 配置字段
    max_retry: int = Field(default=3, description="最大重试次数")
    timeout_seconds: int = Field(default=10, description="请求超时时间（秒）")
    config: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON), description="设备配置")


class DeviceCommand(DeviceCommandBase, DataTableMixin, SoftDeleteMixin, table=True):
    """设备指令记录表

    记录 WES 下发给设备的所有指令，用于追踪和幂等性控制
    """

    __tablename__ = "device_commands"
    __schema__ = SchemaType.SYS.value
    __table_args__ = {"comment": "设备指令记录表"}

    # 重新定义基础字段（使用 SQLModel Field）
    command_id: str = Field(max_length=100, unique=True, nullable=False, description="全局唯一指令 ID")
    device_id: str = Field(
        default=None,
        sa_column=Column(
            String(50), ForeignKey("wes_sys.devices.device_id", ondelete="CASCADE"), nullable=False, index=True
        ),
        description="目标设备 ID",
    )
    task_type: str = Field(max_length=50, nullable=False, description="任务类型")
    priority: int = Field(default=1, description="优先级")
    timeout_ms: int = Field(default=30000, description="超时时间（毫秒）")
    params: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON), description="业务参数")

    # 状态字段
    status: str = Field(default="PENDING", max_length=20, description="指令状态")
    sent_at: datetime | None = Field(default=None, description="发送时间")
    acked_at: datetime | None = Field(default=None, description="确认时间")
    completed_at: datetime | None = Field(default=None, description="完成时间")
    retry_count: int = Field(default=0, description="重试次数")

    # 结果字段（设备回调填充）
    result: str | None = Field(default=None, sa_column=Column(String(20)), description="执行结果")
    result_data: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON), description="结果数据")
    error_code: str | None = Field(default=None, sa_column=Column(String(50)), description="错误码")
    error_message: str | None = Field(default=None, sa_column=Column(Text), description="错误消息")
    device_trace_id: str | None = Field(default=None, sa_column=Column(String(100)), description="设备端日志 ID")

    # 设备返回的原始数据
    raw_response: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON), description="设备响应原始数据")


class DeviceEvent(DeviceEventBase, DataTableMixin, table=True):
    """设备事件表

    记录设备上报的事件（传感器触发、状态变更等）
    """

    __tablename__ = "device_events"
    __schema__ = SchemaType.SYS.value
    __table_args__ = {"comment": "设备事件表"}

    # 重新定义基础字段（使用 SQLModel Field）
    device_id: str = Field(
        default=None,
        sa_column=Column(
            String(50), ForeignKey("wes_sys.devices.device_id", ondelete="CASCADE"), nullable=False, index=True
        ),
        description="设备 ID",
    )
    event_type: str = Field(max_length=50, nullable=False, description="事件类型")
    event_data: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON), description="事件负载数据")

    # 处理状态
    is_processed: bool = Field(default=False, description="是否已处理")
    processed_at: datetime | None = Field(default=None, description="处理时间")


# ============================================================================
# 动态定义关系（避免 Pydantic 类型注解问题）
# ============================================================================

Device.commands = relationship(  # type: ignore[assignment]
    "DeviceCommand", back_populates="device", cascade="all, delete-orphan"
)
Device.events = relationship(  # type: ignore[assignment]
    "DeviceEvent", back_populates="device", cascade="all, delete-orphan"
)
DeviceCommand.device = relationship(  # type: ignore[assignment]
    "Device", back_populates="commands"
)
DeviceEvent.device = relationship(  # type: ignore[assignment]
    "Device", back_populates="events"
)


# ============================================================================
# Pydantic Schema（用于 API 请求/响应）
# ============================================================================


class DeviceCreate(ModelFactory(DeviceBase).for_create()):
    """创建设备请求 Schema"""

    auth_token: str | None = None
    max_retry: int | None = 3
    timeout_seconds: int | None = 10
    config: dict[str, Any] | None = None


class DeviceUpdate(ModelFactory(DeviceBase).for_update()):
    """更新设备请求 Schema"""


class DeviceResponse(DeviceBase):
    """设备响应 Schema"""

    id: int
    is_online: bool
    last_heartbeat: datetime | None
    status: str
    current_command_id: str | None
    max_retry: int
    timeout_seconds: int
    created_at: datetime
    updated_at: datetime


# ============================================================================
# 设备指令相关 Schema
# ============================================================================


class CommandRequest(BaseModel):
    """下发指令请求 Schema"""

    device_id: str
    task_type: str
    priority: int = 1
    timeout_ms: int = 30000
    params: dict[str, Any]


class CommandResponse(DeviceCommandBase):
    """指令响应 Schema"""

    params: dict[str, Any] | None = None
    id: int
    status: str
    sent_at: datetime | None
    acked_at: datetime | None
    completed_at: datetime | None
    result: str | None
    result_data: dict[str, Any] | None
    error_message: str | None
    created_at: datetime


# ============================================================================
# 设备回调相关 Schema（白皮书约定格式）
# ============================================================================


class ResultCallbackRequest(BaseModel):
    """任务结果回调请求（白皮书 3.2.1）"""

    command_id: str
    device_id: str
    result: str
    finish_time: int
    data: dict[str, Any] | None = None
    error_detail: dict[str, str] | None = None


class EventCallbackRequest(BaseModel):
    """设备事件上报请求（白皮书 3.2.2）"""

    device_id: str
    event_type: str
    timestamp: int
    data: dict[str, Any] | None = None


# ============================================================================
# 设备端接口 Schema（供应商实现，WES 调用）
# ============================================================================


class DeviceCommandPayload(BaseModel):
    """WES 下发给设备的指令格式（白皮书 3.1.1）"""

    command_id: str
    task_type: str
    priority: int
    timeout: int
    params: dict[str, Any]
    timestamp: int


class DeviceCommandAck(BaseModel):
    """设备接收指令的响应格式（白皮书 3.1.1）"""

    code: int
    message: str
    trace_id: str | None = None


class DeviceStatusResponse(BaseModel):
    """设备状态查询响应（白皮书 3.1.2）"""

    device_id: str
    status: str
    current_cmd_id: str | None = None
    error_code: str = "NONE"


# ============================================================================
# 统一响应格式（ACK 响应）
# ============================================================================


class AckResponse(BaseModel):
    """ACK 确认响应（白皮书约定）"""

    code: int = 200
    message: str = "ACK"
