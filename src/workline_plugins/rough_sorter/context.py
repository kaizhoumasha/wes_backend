"""粗分机插件 Session context 快照。"""

from typing import Any

from pydantic import BaseModel, Field


class RoughSorterContext(BaseModel):
    """粗分机业务上下文，仅保存可 JSON 序列化快照。"""

    six_in_one: dict[str, Any] = Field(default_factory=dict)
    business_key: str | None = None
    measurement: dict[str, Any] = Field(default_factory=dict)
    wms_validation: dict[str, Any] = Field(default_factory=dict)
    target_bin_location: str | None = None
    rack_operation: dict[str, Any] = Field(default_factory=dict)
    ng_reason: dict[str, Any] = Field(default_factory=dict)
    phase: str | None = None


__all__ = ["RoughSorterContext"]
