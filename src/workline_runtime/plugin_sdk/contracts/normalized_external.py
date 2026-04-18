"""标准化后的外部回调输入。"""

from typing import Any

from pydantic import BaseModel, Field


class NormalizedExternalCallback(BaseModel):
    """标准化外部 HTTP 回调。"""

    callback_type: str
    correlation_id: str | None = None
    source_system: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    attributes: dict[str, Any] = Field(default_factory=dict)


__all__ = ["NormalizedExternalCallback"]
