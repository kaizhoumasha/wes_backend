"""Callback 域诊断构建器 — wlr.diagnostics.builder 镜像。

镜像说明:
- 与 wlr.diagnostics.builder 行为一致 (DEFAULTS 字典 + build_diagnostic_*
  三个函数)。
- _resolve_diagnostic_device_code / _resolve_diagnostic_plugin_key 复用
  TraceContext 内部字段,wirings 由 callback 域本地 TraceContext 提供。
"""

from typing import Any

from src.utils.value_normalization import optional_int_attr, optional_str, optional_str_attr

from .codes import ErrorCode, ErrorDomain, ProblemClass, Recoverability, Severity, error_domain_for
from .models import DiagnosticCard, DiagnosticContext, DiagnosticEvent
from .trace_context import TraceContext


def _resolve_diagnostic_device_code(
    resolved_trace: TraceContext,
    *,
    device: Any | None,
    outbox: Any | None,
) -> str | None:
    return (
        resolved_trace.device_code
        or optional_str_attr(device, "device_code")
        or optional_str_attr(outbox, "target_code")
    )


def _resolve_diagnostic_plugin_key(
    resolved_trace: TraceContext,
    *,
    session: Any | None,
    workline: Any | None,
) -> str | None:
    return (
        resolved_trace.plugin_key
        or optional_str_attr(session, "plugin_key")
        or optional_str_attr(workline, "plugin_key")
    )


_DEFAULTS: dict[ErrorCode, tuple[Severity, Recoverability, ProblemClass, str, list[str]]] = {
    ErrorCode.CALLBACK_SCHEMA_INVALID: (
        Severity.WARNING,
        Recoverability.MANUAL_RETRYABLE,
        ProblemClass.HARDWARE,
        "回调数据格式不符合系统要求，请检查设备或第三方回调报文。",
        ["检查回调 payload 关键字段", "确认设备/第三方协议版本与系统约定一致"],
    ),
    ErrorCode.SESSION_CONTEXT_MISSING: (
        Severity.ERROR,
        Recoverability.MANUAL_INTERVENTION_REQUIRED,
        ProblemClass.SOFTWARE,
        "系统无法恢复当前作业会话，请联系技术支持。",
        ["检查 inbox 归属字段是否完整", "检查 SessionResolver 归属规则"],
    ),
    ErrorCode.SESSION_RESOLVE_FAILED: (
        Severity.ERROR,
        Recoverability.MANUAL_INTERVENTION_REQUIRED,
        ProblemClass.SOFTWARE,
        "系统无法匹配当前业务会话，请联系支持人员。",
        ["检查 business_key / trace_id 归属逻辑", "核对设备与作业线绑定关系"],
    ),
    ErrorCode.PLUGIN_EXECUTION_FAILED: (
        Severity.ERROR,
        Recoverability.MANUAL_RETRYABLE,
        ProblemClass.SOFTWARE,
        "业务插件处理失败，请稍后重试或联系技术支持。",
        ["回放该 inbox 的 normalized input", "检查插件返回结果与状态迁移逻辑"],
    ),
    ErrorCode.PLUGIN_TRANSITION_INVALID: (
        Severity.ERROR,
        Recoverability.MANUAL_RETRYABLE,
        ProblemClass.SOFTWARE,
        "流程状态无法推进，请联系技术支持。",
        ["检查当前 session 状态", "核对 transition 与状态机定义是否匹配"],
    ),
    ErrorCode.CONTRACT_MISMATCH: (
        Severity.ERROR,
        Recoverability.MANUAL_INTERVENTION_REQUIRED,
        ProblemClass.HARDWARE,
        "设备/作业线配置版本与插件契约不一致。",
        ["检查 workline.contract_version", "检查插件 contract_version 与配置是否一致"],
    ),
    ErrorCode.DEVICE_UNREACHABLE: (
        Severity.ERROR,
        Recoverability.MANUAL_RETRYABLE,
        ProblemClass.HARDWARE,
        "设备当前不可达，请检查网络、电源或设备服务。",
        ["检查设备网络连通性", "检查设备服务进程与端口状态"],
    ),
    ErrorCode.DEVICE_TIMEOUT: (
        Severity.ERROR,
        Recoverability.MANUAL_RETRYABLE,
        ProblemClass.HARDWARE,
        "设备响应超时，请检查设备状态和通信链路。",
        ["检查设备响应耗时", "检查 timeout 配置是否合理"],
    ),
    ErrorCode.OUTBOX_ACK_TIMEOUT: (
        Severity.WARNING,
        Recoverability.AUTO_RETRYABLE,
        ProblemClass.HARDWARE,
        "设备派发 ACK 未在通信窗口内返回，系统将按同一 command_code 自动重试。",
        ["检查设备服务网络", "查看 outbox dispatch attempt 失败证据"],
    ),
    ErrorCode.CALLBACK_DEADLINE_EXPIRED: (
        Severity.CRITICAL,
        Recoverability.MANUAL_INTERVENTION_REQUIRED,
        ProblemClass.HARDWARE,
        "设备已 ACK 但执行结果未按时回传，物理状态未知，需人工对账。",
        ["现场确认设备动作状态", "通过 runtime reconciliation resolve 解除隔离"],
    ),
    ErrorCode.OUTBOX_DISPATCH_FAILED: (
        Severity.ERROR,
        Recoverability.MANUAL_INTERVENTION_REQUIRED,
        ProblemClass.HARDWARE,
        "设备派发重试耗尽，无法确认设备是否收到命令，需人工对账。",
        ["核对 command_code 是否被设备接收", "现场确认物理状态后解除隔离"],
    ),
    ErrorCode.INBOX_PROCESSING_TIMEOUT: (
        Severity.ERROR,
        Recoverability.MANUAL_RETRYABLE,
        ProblemClass.SOFTWARE,
        "Inbox worker 处理超时，未完成本次编排事务。",
        ["检查 worker 日志和锁等待", "修复后重试或 replay inbox"],
    ),
    ErrorCode.RESOURCE_WAIT: (
        Severity.WARNING,
        Recoverability.AUTO_RETRYABLE,
        ProblemClass.SOFTWARE,
        "目标资源暂不可用，系统会等待资源释放后自动重试。",
        ["检查 subject_type/subject_key/projection_type 等待证据", "确认目标 subject 的释放事件是否到达"],
    ),
    ErrorCode.WMS_TIMEOUT: (
        Severity.ERROR,
        Recoverability.MANUAL_INTERVENTION_REQUIRED,
        ProblemClass.SOFTWARE,
        "WMS 库存或同步调用超时，请先确认外部系统状态。",
        ["检查 WMS_INVENTORY 请求", "确认 WMS 服务与网络链路", "确认后重新触发测试"],
    ),
    ErrorCode.CONFIG_INVALID: (
        Severity.ERROR,
        Recoverability.MANUAL_INTERVENTION_REQUIRED,
        ProblemClass.HARDWARE,
        "配置不完整或不合法，请先修正配置再继续。",
        ["检查 Device / Workline 主数据配置", "校验插件绑定与通信配置"],
    ),
    ErrorCode.UNKNOWN: (
        Severity.ERROR,
        Recoverability.MANUAL_INTERVENTION_REQUIRED,
        ProblemClass.SOFTWARE,
        "系统出现未分类异常，请联系技术支持。",
        ["检查任务日志", "结合 trace_id 排查全链路"],
    ),
}


def build_diagnostic_context(
    *,
    request_id: str | None = None,
    trace_id: str | None = None,
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
        trace_id=trace_id,
        canonical_event_type=canonical_event_type,
        transition=transition,
    )
    if device is not None:
        resolved_trace = resolved_trace.with_device(device)

    return DiagnosticContext(
        request_id=resolved_trace.request_id or optional_str(request_id),
        trace_id=resolved_trace.trace_id or optional_str(trace_id),
        session_id=resolved_trace.session_id or optional_int_attr(session, "id"),
        inbox_id=resolved_trace.inbox_id or optional_int_attr(inbox, "id"),
        outbox_id=resolved_trace.outbox_id or optional_int_attr(outbox, "id"),
        command_code=resolved_trace.command_code or optional_str_attr(command, "command_code"),
        device_code=_resolve_diagnostic_device_code(resolved_trace, device=device, outbox=outbox),
        workline_id=resolved_trace.workline_id or optional_int_attr(session, "workline_id"),
        workline_code=optional_str_attr(workline, "line_code"),
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
    error_domain: ErrorDomain | None = None,
    problem_class: ProblemClass | None = None,
    technical_summary: str | None = None,
    user_message: str | None = None,
    operator_action: str | None = None,
    next_steps: list[str] | None = None,
) -> DiagnosticEvent:
    """按错误码和上下文构建诊断事件。"""

    severity, recoverability, default_problem_class, default_user_message, default_steps = _DEFAULTS.get(
        error_code,
        _DEFAULTS[ErrorCode.UNKNOWN],
    )
    resolved_error_domain = error_domain or error_domain_for(error_code)
    resolved_problem_class = problem_class or default_problem_class
    return DiagnosticEvent(
        error_code=error_code,
        error_domain=resolved_error_domain,
        severity=severity,
        recoverability=recoverability,
        problem_class=resolved_problem_class,
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
