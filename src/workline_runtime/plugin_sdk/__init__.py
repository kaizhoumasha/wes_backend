"""插件开发 SDK 基础导出。"""

from .classifiers.result_classifier import (
    ResultClassification,
    classify_result,
    classify_result_category,
    normalize_result_classification,
)
from .contracts import (
    NormalizedCommandResult,
    NormalizedDeviceEvent,
    NormalizedExternalCallback,
    ResolvedDeviceRuntimeConfig,
    ResolvedExecutionContext,
    ResolvedWorklineRuntimeConfig,
    resolve_device_runtime_config,
    resolve_execution_context,
    resolve_workline_runtime_config,
)
from .normalizers.event_mapper import canonicalize_event_type
from .normalizers.input_normalizer import normalize_inbox_input

__all__ = [
    "NormalizedCommandResult",
    "NormalizedDeviceEvent",
    "NormalizedExternalCallback",
    "ResolvedDeviceRuntimeConfig",
    "ResolvedExecutionContext",
    "ResolvedWorklineRuntimeConfig",
    "ResultClassification",
    "canonicalize_event_type",
    "classify_result",
    "classify_result_category",
    "normalize_inbox_input",
    "normalize_result_classification",
    "resolve_device_runtime_config",
    "resolve_execution_context",
    "resolve_workline_runtime_config",
]
