# 旧 plugin runtime 镜像实现:src.workline_runtime.plugin_sdk.contracts.normalized_event 的平级副本
# 旧 runtime 入口删除后,本模块承载对应正式实现。

"""标准化后的设备事件输入。"""

from typing import Any

from pydantic import BaseModel, Field


class NormalizedDeviceEvent(BaseModel):
    """标准化设备事件。"""

    source_event_type: str
    canonical_event_type: str
    device_code: str | None = None
    business_key: str | None = None
    trace_id: str | None = None
    event_time: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)
    attributes: dict[str, Any] = Field(default_factory=dict)


__all__ = ["NormalizedDeviceEvent"]
