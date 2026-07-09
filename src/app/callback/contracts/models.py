"""Callback 域诊断模型 — legacy runtime.diagnostics.models 镜像。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .codes import ErrorCode, ErrorDomain, ProblemClass, Recoverability, Severity


class DiagnosticContext(BaseModel):
    """贯穿 callback → inbox → orchestrator → outbox 的统一上下文。"""

    request_id: str | None = None
    trace_id: str | None = None
    session_id: int | None = None
    inbox_id: int | None = None
    outbox_id: int | None = None
    command_code: str | None = None
    device_code: str | None = None
    workline_id: int | None = None
    workline_code: str | None = None
    plugin_key: str | None = None
    canonical_event_type: str | None = None
    transition: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class DiagnosticEvent(BaseModel):
    """结构化诊断事实。"""

    error_code: ErrorCode
    error_domain: ErrorDomain
    severity: Severity = Severity.ERROR
    recoverability: Recoverability = Recoverability.MANUAL_RETRYABLE
    problem_class: ProblemClass = ProblemClass.SOFTWARE
    message: str
    technical_summary: str | None = None
    user_message: str | None = None
    operator_action: str | None = None
    next_steps: list[str] = Field(default_factory=list)
    context: DiagnosticContext


class DiagnosticCard(BaseModel):
    """供日志、页面和回放统一消费的诊断卡片。"""

    title: str
    summary: str
    error_code: ErrorCode
    error_domain: ErrorDomain
    severity: Severity
    recoverability: Recoverability
    problem_class: ProblemClass
    user_message: str
    operator_action: str | None = None
    technical_summary: str | None = None
    next_steps: list[str] = Field(default_factory=list)
    context: DiagnosticContext


__all__ = ["DiagnosticCard", "DiagnosticContext", "DiagnosticEvent"]
