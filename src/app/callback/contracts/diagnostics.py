"""Callback 域诊断模块 — wlr.diagnostics 镜像 (Phase 2 launch PR)。"""

from .builder import build_diagnostic_card, build_diagnostic_context, build_diagnostic_event
from .codes import ErrorCode, ErrorDomain, ProblemClass, Recoverability, Severity, error_domain_for
from .failure_mapper import map_failure_to_diagnostic
from .models import DiagnosticCard, DiagnosticContext, DiagnosticEvent
from .registry import (
    DiagnosticCodeDefinition,
    get_diagnostic_code_definition,
    list_diagnostic_code_definitions,
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
