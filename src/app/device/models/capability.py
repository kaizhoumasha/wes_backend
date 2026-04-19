"""Device 能力声明模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DeviceCapabilityProfile(BaseModel):
    """设备最小可用能力声明。"""

    supports_event_types: list[str] = Field(default_factory=list)
    supports_command_types: list[str] = Field(default_factory=list)
    supports_result_callback: bool | None = None
    supports_ack_response: bool | None = None
    supports_cancel: bool | None = None

    def supports_event(self, event_type: str) -> bool:
        """事件能力校验：未配置时保持兼容，按允许策略通过。"""

        if not self.supports_event_types:
            return True
        return event_type in self.supports_event_types

    def supports_command(self, command_type: str | None) -> bool:
        """命令能力校验：未配置或 command_type 为空时保持兼容。"""

        if not command_type or not self.supports_command_types:
            return True
        return command_type in self.supports_command_types

    def allows_result_callback(self) -> bool:
        """结果回调校验：未配置时保持兼容。"""

        if self.supports_result_callback is None:
            return True
        return self.supports_result_callback


def parse_device_capabilities(value: Any) -> DeviceCapabilityProfile:
    """解析设备能力声明。"""

    if value is None:
        return DeviceCapabilityProfile()
    if not isinstance(value, dict):
        raise TypeError("Input should be a valid dictionary")
    return DeviceCapabilityProfile.model_validate(value)


__all__ = ["DeviceCapabilityProfile", "parse_device_capabilities"]
