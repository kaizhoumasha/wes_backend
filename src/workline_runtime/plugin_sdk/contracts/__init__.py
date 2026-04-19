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
