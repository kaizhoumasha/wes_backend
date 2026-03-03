"""
设备事件日志模型 (Device Event Log)

用于记录设备事件上报的历史记录，支持事件溯源和问题排查。
遵循白皮书 3.2.2 节规范。

相关文档:
- 白皮书: @docs/third_party_integration_whitepaper.md
"""

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import JSON, Column, Text
from sqlmodel import Field as SQLField
from sqlmodel import Relationship

from src.core.mixins import DataTableMixin, SoftDeleteMixin
from src.database.schema_conf import SchemaType

if TYPE_CHECKING:
    from src.app.device.models.device import Device

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


# ==================== Pydantic Schema ====================


class EventRequest(BaseModel):
    """事件上报请求 Schema - 设备回调时使用"""

    device_id: str = Field(description="设备 ID")
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
    device_id: str
    event_type: EventType
    event_data: dict[str, Any] | None
    processed: bool
    processing_result: dict[str, Any] | None
    error_message: str | None
    created_at: datetime


# ==================== 数据库表模型 ====================


class DeviceEventLog(DataTableMixin, SoftDeleteMixin, table=True):
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

    __tablename__: str = "device_event_logs"
    __schema__ = SchemaType.BIZ.value  # 业务数据表

    # 设备信息
    device_id: str = SQLField(
        max_length=50,
        index=True,
        description="设备 ID",
    )

    # 事件信息
    event_type: EventType = SQLField(
        index=True,
        description="事件类型",
    )
    event_timestamp: datetime = SQLField(
        index=True,
        description="设备上报的事件时间",
    )
    event_data: dict[str, Any] | None = SQLField(
        default=None,
        sa_column=Column(JSON),
        description="事件负载数据（JSON 格式）",
    )

    # 处理状态
    processed: bool = SQLField(
        default=False,
        index=True,
        description="是否已处理",
    )
    processing_result: dict[str, Any] | None = SQLField(
        default=None,
        sa_column=Column(JSON),
        description="处理结果（JSON 格式）",
    )
    error_message: str | None = SQLField(
        default=None,
        sa_column=Column(Text),
        description="错误消息",
    )

    # 关联 ID（串联相关事件和指令）
    correlation_id: str | None = SQLField(
        default=None,
        max_length=100,
        index=True,
        description="关联 ID（串联相关事件和指令）",
    )

    # 关系定义
    device: "Device" = Relationship(  # type: ignore[assignment]
        sa_relationship_kwargs={
            "lazy": "selectin",
            "foreign_keys": ["DeviceEventLog.device_id"],
        }
    )


# ==================== 导出 ====================


__all__ = [
    "DeviceEventLog",
    "EventRequest",
    "EventResponse",
    "EventType",
]
