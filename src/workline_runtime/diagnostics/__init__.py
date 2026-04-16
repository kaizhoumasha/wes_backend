"""作业线运行时诊断工具包。"""

from .builder import build_diagnostic_card, build_diagnostic_context, build_diagnostic_event
from .codes import ErrorCode, ErrorDomain, OwnerRole, Recoverability, Severity
from .models import DiagnosticCard, DiagnosticContext, DiagnosticEvent
from .projectors import project_card_for_role

__all__ = [
    "DiagnosticCard",
    "DiagnosticContext",
    "DiagnosticEvent",
    "ErrorCode",
    "ErrorDomain",
    "OwnerRole",
    "Recoverability",
    "Severity",
    "build_diagnostic_card",
    "build_diagnostic_context",
    "build_diagnostic_event",
    "project_card_for_role",
]
