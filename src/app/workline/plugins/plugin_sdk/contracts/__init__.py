# 阶段 2 burn-down C5b 镜像:src.workline_runtime.plugin_sdk.contracts 的平级副本
# wlr 目录在阶段 3 整体删除时,本包与 wlr 包合并 / 删除。

"""插件 SDK 契约模型。"""

from .normalized_event import NormalizedDeviceEvent
from .normalized_external import NormalizedExternalCallback
from .normalized_result import NormalizedCommandResult
from .runtime_config import (
    ResolvedDeviceRuntimeConfig,
    ResolvedExecutionContext,
    ResolvedWorklineRuntimeConfig,
    resolve_device_runtime_config,
    resolve_execution_context,
    resolve_workline_runtime_config,
)

__all__ = [
    "NormalizedCommandResult",
    "NormalizedDeviceEvent",
    "NormalizedExternalCallback",
    "ResolvedDeviceRuntimeConfig",
    "ResolvedExecutionContext",
    "ResolvedWorklineRuntimeConfig",
    "resolve_device_runtime_config",
    "resolve_execution_context",
    "resolve_workline_runtime_config",
]
