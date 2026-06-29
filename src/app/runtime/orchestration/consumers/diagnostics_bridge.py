"""阶段 2 burn-down C2 — diagnostics_bridge 聚合 re-export。

wlr diagnostics 包(6 子文件,16 公开符号)聚合暴露给 runtime/orchestration 域。
wlr 目录在阶段 3 整体删除时,本 bridge 不变。

不重新 export 实现细节(子模块路径:`builder`/`codes`/`failure_mapper`/`models`/`registry`),
只聚合 wlr `__all__` 列表中的公开符号。
"""

from src.workline_runtime.diagnostics import (
    DiagnosticCard,
    DiagnosticCodeDefinition,
    DiagnosticContext,
    DiagnosticEvent,
    ErrorCode,
    ErrorDomain,
    ProblemClass,
    Recoverability,
    Severity,
    build_diagnostic_card,
    build_diagnostic_context,
    build_diagnostic_event,
    error_domain_for,
    get_diagnostic_code_definition,
    list_diagnostic_code_definitions,
    map_failure_to_diagnostic,
)

__all__ = [
    "DiagnosticCard",
    "DiagnosticCodeDefinition",
    "DiagnosticContext",
    "DiagnosticEvent",
    "ErrorCode",
    "ErrorDomain",
    "ProblemClass",
    "Recoverability",
    "Severity",
    "build_diagnostic_card",
    "build_diagnostic_context",
    "build_diagnostic_event",
    "error_domain_for",
    "get_diagnostic_code_definition",
    "list_diagnostic_code_definitions",
    "map_failure_to_diagnostic",
]
