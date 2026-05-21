"""Callback event request models."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CallbackEventRequest(BaseModel):
    """Minimal callback event envelope.

    该模型只负责 `/callback/event` 的最小包络校验：
    - `device_code`
    - `event_type`
    - `timestamp`
    - `data`

    它不承担插件私有 payload 语义校验，例如：
    - `SCAN_COMPLETED` 是否带齐业务字段
    - `SixInOne` 是否可解析
    - 插件字段别名是否映射成功
    """

    model_config = ConfigDict(extra="forbid")

    device_code: str = Field(description="设备编码（device_code，设备标识）")
    event_type: str = Field(description="事件类型（具体合法值由 plugin contract 决定）")
    timestamp: int | None = Field(
        default=None,
        description="事件时间戳（Unix 时间戳，毫秒）。设备无时钟可不传，服务器将使用接收时间",
    )
    data: dict[str, Any] | None = Field(default=None, description="事件负载数据")
    trace_id: str | None = Field(default=None, description="统一 Trace ID")
    event_id: str | None = Field(default=None, description="供应商事件 ID")
    causation_id: str | None = Field(default=None, description="因果事件 ID")

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, value: int | None) -> int | None:
        """验证时间戳合理性。"""

        if value is None:
            return None
        if not (1577836800000 <= value <= 1924991999000):
            raise ValueError("时间戳不在合理范围内")
        return value


__all__ = ["CallbackEventRequest"]
