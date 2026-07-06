# 阶段 2 burn-down C5b 镜像:src.workline_runtime.plugin_sdk.contracts.normalized_external 的平级副本
# wlr 目录在阶段 3 整体删除时,本镜像与 wlr 副本合并 / 删除。

"""标准化后的外部回调输入。"""

from typing import Any

from pydantic import BaseModel, Field


class NormalizedExternalCallback(BaseModel):
    """标准化外部 HTTP 回调。"""

    callback_type: str
    runtime_capability: str | None = None
    trace_id: str | None = None
    source_system: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    attributes: dict[str, Any] = Field(default_factory=dict)


__all__ = ["NormalizedExternalCallback"]
