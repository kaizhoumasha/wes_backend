# 旧 plugin runtime 镜像实现:src.workline_runtime.plugin_sdk.contracts.normalized_result 的平级副本
# 旧 runtime 入口删除后,本模块承载对应正式实现。

"""标准化后的命令结果输入。"""

from typing import Any

from pydantic import BaseModel, Field


class NormalizedCommandResult(BaseModel):
    """标准化命令结果。"""

    command_code: str
    source_result: str
    normalized_result: str
    result_classification: str | None = None
    command_type: str | None = None
    device_code: str | None = None
    trace_id: str | None = None
    finish_time: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)
    error_detail: dict[str, Any] = Field(default_factory=dict)


__all__ = ["NormalizedCommandResult"]
