"""运行时诊断模块门面 — 聚合层 re-export。

阶段 3 burn-down C1 迁出 consumers/ trust zone;原 diagnostics_bridge.py 改名 + 改 import 路径。
diagnostics 子包(6 子文件,16 公开符号)已在本地 `src/app/runtime/orchestration/diagnostics/`,
聚合层仅 re-export `__all__` 列表中的公开符号。
"""

from src.app.runtime.orchestration.diagnostics import (
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
