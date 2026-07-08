# runtime migration C5b 镜像:src.workline_runtime.plugin_sdk.contracts.normalized_event 的平级副本
# wlr 目录在阶段 3 整体删除时,本镜像与 wlr 副本合并 / 删除。

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
