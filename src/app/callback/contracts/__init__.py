"""Callback 域 contracts（跨域 import 解耦镜像）。

本包承载 callback 域真正使用的诊断契约 (ErrorCode/ErrorDomain/...)
与运行时事件 / TraceContext / 时间线生成器。

来源镜像自 `src.workline_runtime.{diagnostics,trace_context,runtime_events,
timeline_generator,plugin_sdk.normalizers.event_mapper}`,
目的是切断 callback 域对 `src.workline_runtime` 的反向依赖 (主计划 §10.3
跨域修复要求)。

约束:
- 这是镜像副本,与 wlr 源保持契约对齐 (ErrorCode 枚举值不变,
  build_diagnostic_* 函数签名不变,canonicalize_event_type 的生产事件
  source 映射行为不变; callback ingress 额外保留平台/安全事件 source 原值)。
- runtime 重构收口时会与 `src.app.runtime.orchestration` 的 contracts
  合并,届时本包整体迁入 runtime orchestration 域。
- 本模块不导入 `src.workline_runtime.*`,内部依赖仅限 `src.utils.*` 与
  本包内其它模块。
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
]
