"""作业线运行时诊断工具包。"""

from .builder import build_diagnostic_card, build_diagnostic_context, build_diagnostic_event
from .codes import ErrorCode, ErrorDomain, ProblemClass, Recoverability, Severity
from .failure_mapper import map_failure_to_diagnostic
from .models import DiagnosticCard, DiagnosticContext, DiagnosticEvent

__all__ = [
    "DiagnosticCard",
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
    "map_failure_to_diagnostic",
]
