"""Callback 域 contracts（跨域 import 解耦镜像）。

本包承载 callback 入站边界使用的诊断契约、运行时事件、TraceContext
与时间线生成器。诊断模型和错误码与
``src.app.runtime.orchestration.diagnostics`` 保持当前合同对称。

约束:
- 两侧只同步当前诊断语义；字段或错误码退役时必须同时收敛，不保留 legacy 兼容入口。
- ``canonicalize_event_type`` 保持生产事件 source 映射行为；callback ingress
  额外保留平台与安全事件 source 原值。
- 本地诊断镜像不反向导入 runtime diagnostics 实现；``TimelineGenerator`` 明确复用
  runtime orchestration 的共享 timeline 模型。
"""

from __future__ import annotations

from .builder import (
    build_diagnostic_card,
    build_diagnostic_context,
    build_diagnostic_event,
)
from .codes import (
    ErrorCode,
    ErrorDomain,
    ProblemClass,
    Recoverability,
    Severity,
    error_domain_for,
)
from .event_mapper import canonicalize_event_type
from .external_callbacks import (
    WMS_ALLOWED_CALLBACK_TYPES,
    WMS_ORDINARY_EVENT_TYPES,
    validate_external_callback_type,
)
from .failure_mapper import map_failure_to_diagnostic
from .models import DiagnosticCard, DiagnosticContext, DiagnosticEvent
from .registry import (
    DiagnosticCodeDefinition,
    get_diagnostic_code_definition,
    list_diagnostic_code_definitions,
)
from .runtime_events import (
    PLATFORM_CONTROL_EVENTS,
    RESERVED_RUNTIME_EVENTS,
    assert_not_reserved_runtime_event,
    is_platform_control_event,
    is_platform_safety_event,
    is_production_event,
    is_reserved_runtime_event,
)
from .timeline_generator import TimelineGenerator, timeline_generator
from .trace_context import TraceContext

__all__ = [
    "PLATFORM_CONTROL_EVENTS",
    "RESERVED_RUNTIME_EVENTS",
    "WMS_ALLOWED_CALLBACK_TYPES",
    "WMS_ORDINARY_EVENT_TYPES",
    "DiagnosticCard",
    "DiagnosticCodeDefinition",
    "DiagnosticContext",
    "DiagnosticEvent",
    "ErrorCode",
    "ErrorDomain",
    "ProblemClass",
    "Recoverability",
    "Severity",
    "TimelineGenerator",
    "TraceContext",
    "assert_not_reserved_runtime_event",
    "build_diagnostic_card",
    "build_diagnostic_context",
    "build_diagnostic_event",
    "canonicalize_event_type",
    "error_domain_for",
    "get_diagnostic_code_definition",
    "is_platform_control_event",
    "is_platform_safety_event",
    "is_production_event",
    "is_reserved_runtime_event",
    "list_diagnostic_code_definitions",
    "map_failure_to_diagnostic",
    "timeline_generator",
    "validate_external_callback_type",
]
