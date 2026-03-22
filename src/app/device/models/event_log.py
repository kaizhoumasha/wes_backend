"""
设备事件日志模型 (Device Event Log)

用于记录设备事件上报的历史记录，支持事件溯源和问题排查。
遵循白皮书 3.2.2 节规范。

相关文档:
- 白皮书: @docs/third_party_integration_whitepaper.md
"""

from datetime import datetime
from enum import Enum
from typing import Any, ClassVar, cast

from pydantic import BaseModel, field_validator
from sqlalchemy import JSON, Column, Text
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field, Relationship

from src.app.device.models.device import (
    Device,  # noqa: TC001 - runtime import ensures related device/workline metadata loads
)
from src.core.mixins import BaseMixin, DataTableMixin, SoftDeleteMixin
from src.database.schema_conf import SchemaType

# ==================== 枚举定义 ====================


class EventType(str, Enum):
    """事件类型枚举 (白皮书 3.2.2)"""

    # 设备状态事件
    ESTOP_PRESSED = "ESTOP_PRESSED"  # 急停按下
    DEVICE_ONLINE = "DEVICE_ONLINE"  # 设备上线
    DEVICE_OFFLINE = "DEVICE_OFFLINE"  # 设备离线
    DEVICE_ERROR = "DEVICE_ERROR"  # 设备故障

    # 业务触发事件
    MATERIAL_ARRIVED = "MATERIAL_ARRIVED"  # 料盘/物料到达
    SCAN_COMPLETED = "SCAN_COMPLETED"  # 扫码完成
    PICK_COMPLETED = "PICK_COMPLETED"  # 抓取完成
    PUT_COMPLETED = "PUT_COMPLETED"  # 放置完成
    PROCESS_COMPLETED = "PROCESS_COMPLETED"  # 加工完成


# ==================== 基础字段 (用于 Schema 复用) ====================


class DeviceEventLogBase(BaseMixin):
    """设备事件日志基础字段 - 用于 Schema 复用"""

    device_id: int = Field(
        index=True,
        foreign_key="wes_biz.devices.id",
        description="设备 ID（关联 Device.id）",
    )

    # 🔥 使用 VARCHAR + CHECK 约束
    event_type: EventType = Field(
        index=True,
        sa_type=cast(
            "Any",
            SQLAEnum(
                EventType,
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
        ),
        description="事件类型",
    )

    event_timestamp: datetime = Field(
        index=True,
        description="设备上报的事件时间",
    )
    event_data: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSON),
        description="事件负载数据（JSON 格式）",
    )


# ==================== Pydantic Schema ====================


class EventRequest(BaseModel):
    """事件上报请求 Schema - 设备回调时使用"""

    device_code: str = Field(description="设备编码（device_code，设备标识）")
    event_type: EventType = Field(description="事件类型")
    timestamp: int | None = Field(
        default=None,
        description="事件时间戳（Unix 时间戳，毫秒）。设备无时钟可不传，服务器将使用接收时间",
    )
    data: dict[str, Any] | None = Field(default=None, description="事件负载数据")

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, v: int | None) -> int | None:
        """验证时间戳合理性"""
        if v is None:
            return None
        # 2020-01-01 到 2030-12-31 之间
        if not (1577836800000 <= v <= 1924991999000):
            raise ValueError("时间戳不在合理范围内")
        return v


class EventResponse(BaseModel):
    """事件响应 Schema - 返回给客户端"""

    id: int
    device_id: int
    event_type: EventType
    event_data: dict[str, Any] | None
    processed: bool
    processing_result: dict[str, Any] | None
    error_message: str | None
    created_at: datetime


# ==================== 数据库表模型 ====================


class DeviceEventLog(
    DeviceEventLogBase,
    DataTableMixin,
    SoftDeleteMixin,
    table=True,
):
    """
    设备事件日志数据库表模型

    记录设备事件上报的完整历史，支持事件溯源和问题排查。

    字段说明:
    - device_id: 设备 ID
    - event_type: 事件类型
    - event_timestamp: 设备上报的事件时间
    - event_data: 事件负载数据（JSON）
    - processed: 是否已处理
    - processing_result: 处理结果（JSON）
    - error_message: 错误消息
    - correlation_id: 关联 ID（串联相关事件和指令）
    """

    __tablename__: ClassVar[str] = "device_event_logs"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value  # 业务数据表

    # 处理状态（不在 Base 中，因为这是事件日志特有的）
    processed: bool = Field(
        default=False,
        index=True,
        description="是否已处理",
    )
    processing_result: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSON),
        description="处理结果（JSON 格式）",
    )
    error_message: str | None = Field(
        default=None,
        sa_column=Column(Text),
        description="错误消息",
    )

    # 关联 ID（串联相关事件和指令）
    correlation_id: str | None = Field(
        default=None,
        max_length=100,
        index=True,
        description="关联 ID（串联相关事件和指令）",
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

    # 关系定义
    device: "Device" = Relationship(  # type: ignore[assignment]
        back_populates=None,
        sa_relationship_kwargs={
            "lazy": "selectin",
            "foreign_keys": "DeviceEventLog.device_id",
            "primaryjoin": "DeviceEventLog.device_id == Device.id",
        },
    )


# ==================== 导出 ====================


__all__ = [
    "DeviceEventLog",
    "DeviceEventLogBase",
    "EventRequest",
    "EventResponse",
    "EventType",
]
