"""诊断构建器。"""

from typing import Any

from src.workline_runtime.trace_context import TraceContext

from .codes import ErrorCode, ErrorDomain, ProblemClass, Recoverability, Severity
from .models import DiagnosticCard, DiagnosticContext, DiagnosticEvent


def _safe_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _resolve_diagnostic_device_code(
    resolved_trace: TraceContext,
    *,
    device: Any | None,
    outbox: Any | None,
) -> str | None:
    return (
        resolved_trace.device_code
        or _safe_str(getattr(device, "device_code", None))
        or _safe_str(getattr(outbox, "target_code", None))
    )


def _resolve_diagnostic_plugin_key(
    resolved_trace: TraceContext,
    *,
    session: Any | None,
    workline: Any | None,
) -> str | None:
    return (
        resolved_trace.plugin_key
        or _safe_str(getattr(session, "plugin_key", None))
        or _safe_str(getattr(workline, "plugin_key", None))
    )


_DEFAULTS: dict[ErrorCode, tuple[ErrorDomain, Severity, Recoverability, ProblemClass, str, list[str]]] = {
    ErrorCode.CALLBACK_SCHEMA_INVALID: (
        ErrorDomain.DATA_QUALITY,
        Severity.WARNING,
        Recoverability.MANUAL_RETRYABLE,
        ProblemClass.HARDWARE,
        "回调数据格式不符合系统要求，请检查设备或第三方回调报文。",
        ["检查回调 payload 关键字段", "确认设备/第三方协议版本与系统约定一致"],
    ),
    ErrorCode.SESSION_CONTEXT_MISSING: (
        ErrorDomain.WORKFLOW,
        Severity.ERROR,
        Recoverability.MANUAL_INTERVENTION_REQUIRED,
        ProblemClass.SOFTWARE,
        "系统无法恢复当前作业会话，请联系技术支持。",
        ["检查 inbox 归属字段是否完整", "检查 SessionResolver 归属规则"],
    ),
    ErrorCode.SESSION_RESOLVE_FAILED: (
        ErrorDomain.WORKFLOW,
        Severity.ERROR,
        Recoverability.MANUAL_INTERVENTION_REQUIRED,
        ProblemClass.SOFTWARE,
        "系统无法匹配当前业务会话，请联系支持人员。",
        ["检查 business_key / correlation_id 归属逻辑", "核对设备与作业线绑定关系"],
    ),
    ErrorCode.PLUGIN_EXECUTION_FAILED: (
        ErrorDomain.PLUGIN,
        Severity.ERROR,
        Recoverability.MANUAL_RETRYABLE,
        ProblemClass.SOFTWARE,
        "业务插件处理失败，请稍后重试或联系技术支持。",
        ["回放该 inbox 的 normalized input", "检查插件返回结果与状态迁移逻辑"],
    ),
    ErrorCode.PLUGIN_TRANSITION_INVALID: (
        ErrorDomain.PLUGIN,
        Severity.ERROR,
        Recoverability.MANUAL_RETRYABLE,
        ProblemClass.SOFTWARE,
        "流程状态无法推进，请联系技术支持。",
        ["检查当前 session 状态", "核对 transition 与状态机定义是否匹配"],
    ),
    ErrorCode.CONTRACT_MISMATCH: (
        ErrorDomain.CONFIG,
        Severity.ERROR,
        Recoverability.MANUAL_INTERVENTION_REQUIRED,
        ProblemClass.HARDWARE,
        "设备/作业线配置版本与插件契约不一致。",
        ["检查 workline.contract_version", "检查插件 contract_version 与配置是否一致"],
    ),
    ErrorCode.DEVICE_UNREACHABLE: (
        ErrorDomain.DEVICE,
        Severity.ERROR,
        Recoverability.MANUAL_RETRYABLE,
        ProblemClass.HARDWARE,
        "设备当前不可达，请检查网络、电源或设备服务。",
        ["检查设备网络连通性", "检查设备服务进程与端口状态"],
    ),
    ErrorCode.DEVICE_TIMEOUT: (
        ErrorDomain.NETWORK,
        Severity.ERROR,
        Recoverability.MANUAL_RETRYABLE,
        ProblemClass.HARDWARE,
        "设备响应超时，请检查设备状态和通信链路。",
        ["检查设备响应耗时", "检查 timeout 配置是否合理"],
    ),
    ErrorCode.OUTBOX_DISPATCH_FAILED: (
        ErrorDomain.INTEGRATION,
        Severity.ERROR,
        Recoverability.AUTO_RETRYABLE,
        ProblemClass.HARDWARE,
        "系统向设备或外部系统派发失败。",
        ["检查派发目标配置", "检查最近 outbox 失败记录"],
    ),
    ErrorCode.CONFIG_INVALID: (
        ErrorDomain.CONFIG,
        Severity.ERROR,
        Recoverability.MANUAL_INTERVENTION_REQUIRED,
        ProblemClass.HARDWARE,
        "配置不完整或不合法，请先修正配置再继续。",
        ["检查 Device / Workline 主数据配置", "校验插件绑定与通信配置"],
    ),
    ErrorCode.UNKNOWN: (
        ErrorDomain.SYSTEM,
        Severity.ERROR,
        Recoverability.MANUAL_INTERVENTION_REQUIRED,
        ProblemClass.SOFTWARE,
        "系统出现未分类异常，请联系技术支持。",
        ["检查任务日志", "结合 correlation_id 排查全链路"],
    ),
}


def build_diagnostic_context(
    *,
    request_id: str | None = None,
    correlation_id: str | None = None,
    session: Any | None = None,
    inbox: Any | None = None,
    outbox: Any | None = None,
    command: Any | None = None,
    device: Any | None = None,
    workline: Any | None = None,
    canonical_event_type: str | None = None,
    transition: str | None = None,
    extra: dict[str, Any] | None = None,
    trace: TraceContext | None = None,
) -> DiagnosticContext:
    """从运行时实体提取统一诊断上下文。"""

    resolved_trace = trace or TraceContext.from_runtime(
        session=session,
        workline=workline,
        inbox=inbox,
        command=command,
        outbox=outbox,
        request_id=request_id,
        correlation_id=correlation_id,
        canonical_event_type=canonical_event_type,
        transition=transition,
    )
    if device is not None:
        resolved_trace = resolved_trace.with_device(device)

    return DiagnosticContext(
        request_id=resolved_trace.request_id or _safe_str(request_id),
        correlation_id=resolved_trace.correlation_id or _safe_str(correlation_id),
        session_id=resolved_trace.session_id or _safe_int(getattr(session, "id", None)),
        inbox_id=resolved_trace.inbox_id or _safe_int(getattr(inbox, "id", None)),
        outbox_id=resolved_trace.outbox_id or _safe_int(getattr(outbox, "id", None)),
        command_code=resolved_trace.command_code or _safe_str(getattr(command, "command_code", None)),
        device_code=_resolve_diagnostic_device_code(resolved_trace, device=device, outbox=outbox),
        workline_id=resolved_trace.workline_id or _safe_int(getattr(session, "workline_id", None)),
        workline_code=_safe_str(getattr(workline, "line_code", None)),
        plugin_key=_resolve_diagnostic_plugin_key(resolved_trace, session=session, workline=workline),
        canonical_event_type=resolved_trace.canonical_event_type,
        transition=resolved_trace.transition,
        extra=extra or {},
    )


def build_diagnostic_event(
    *,
    error_code: ErrorCode,
    context: DiagnosticContext,
    message: str,
    technical_summary: str | None = None,
    user_message: str | None = None,
    operator_action: str | None = None,
    next_steps: list[str] | None = None,
) -> DiagnosticEvent:
    """按错误码和上下文构建诊断事件。"""

    error_domain, severity, recoverability, problem_class, default_user_message, default_steps = _DEFAULTS.get(
        error_code,
        _DEFAULTS[ErrorCode.UNKNOWN],
    )
    return DiagnosticEvent(
        error_code=error_code,
        error_domain=error_domain,
        severity=severity,
        recoverability=recoverability,
        problem_class=problem_class,
        message=message,
        technical_summary=technical_summary or message,
        user_message=user_message or default_user_message,
        operator_action=operator_action,
        next_steps=next_steps or list(default_steps),
        context=context,
    )


def build_diagnostic_card(event: DiagnosticEvent) -> DiagnosticCard:
    """将诊断事件投影为统一诊断卡片。"""

    return DiagnosticCard(
        title=event.error_code.value,
        summary=event.message,
        error_code=event.error_code,
        error_domain=event.error_domain,
        severity=event.severity,
        recoverability=event.recoverability,
        problem_class=event.problem_class,
        user_message=event.user_message or event.message,
        operator_action=event.operator_action,
        technical_summary=event.technical_summary,
        next_steps=list(event.next_steps),
        context=event.context,
    )


__all__ = ["build_diagnostic_card", "build_diagnostic_context", "build_diagnostic_event"]
