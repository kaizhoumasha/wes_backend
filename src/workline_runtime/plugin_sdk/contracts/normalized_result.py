"""标准化后的命令结果输入。"""

from typing import Any

from pydantic import BaseModel, Field


class NormalizedCommandResult(BaseModel):
    """标准化命令结果。"""

    command_code: str
    source_result: str
    normalized_result: str
    command_type: str | None = None
    device_code: str | None = None
    correlation_id: str | None = None
    finish_time: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)
    error_detail: dict[str, Any] = Field(default_factory=dict)


__all__ = ["NormalizedCommandResult"]
