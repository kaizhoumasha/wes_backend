"""诊断构建器。"""

from typing import Any

from .codes import ErrorCode, ErrorDomain, OwnerRole, Recoverability, Severity
from .models import DiagnosticCard, DiagnosticContext, DiagnosticEvent

_DEFAULTS: dict[ErrorCode, tuple[ErrorDomain, Severity, Recoverability, OwnerRole, str, list[str]]] = {
    ErrorCode.CALLBACK_SCHEMA_INVALID: (
        ErrorDomain.DATA_QUALITY,
        Severity.WARNING,
        Recoverability.MANUAL_RETRYABLE,
        OwnerRole.IMPLEMENTATION_ENGINEER,
        "回调数据格式不符合系统要求，请检查设备或第三方回调报文。",
        ["检查回调 payload 关键字段", "确认设备/第三方协议版本与系统约定一致"],
    ),
    ErrorCode.SESSION_CONTEXT_MISSING: (
        ErrorDomain.WORKFLOW,
        Severity.ERROR,
        Recoverability.MANUAL_INTERVENTION_REQUIRED,
        OwnerRole.BACKEND_ENGINEER,
        "系统无法恢复当前作业会话，请联系技术支持。",
        ["检查 inbox 归属字段是否完整", "检查 SessionResolver 归属规则"],
    ),
    ErrorCode.SESSION_RESOLVE_FAILED: (
        ErrorDomain.WORKFLOW,
        Severity.ERROR,
        Recoverability.MANUAL_INTERVENTION_REQUIRED,
        OwnerRole.PLUGIN_DEVELOPER,
        "系统无法匹配当前业务会话，请联系支持人员。",
        ["检查 business_key / correlation_id 归属逻辑", "核对设备与作业线绑定关系"],
    ),
    ErrorCode.PLUGIN_EXECUTION_FAILED: (
        ErrorDomain.PLUGIN,
        Severity.ERROR,
        Recoverability.MANUAL_RETRYABLE,
        OwnerRole.PLUGIN_DEVELOPER,
        "业务插件处理失败，请稍后重试或联系技术支持。",
        ["回放该 inbox 的 normalized input", "检查插件返回结果与状态迁移逻辑"],
    ),
    ErrorCode.PLUGIN_TRANSITION_INVALID: (
        ErrorDomain.PLUGIN,
        Severity.ERROR,
        Recoverability.MANUAL_RETRYABLE,
        OwnerRole.PLUGIN_DEVELOPER,
        "流程状态无法推进，请联系技术支持。",
        ["检查当前 session 状态", "核对 transition 与状态机定义是否匹配"],
    ),
    ErrorCode.CONTRACT_MISMATCH: (
        ErrorDomain.CONFIG,
        Severity.ERROR,
        Recoverability.MANUAL_INTERVENTION_REQUIRED,
        OwnerRole.IMPLEMENTATION_ENGINEER,
        "设备/作业线配置版本与插件契约不一致。",
        ["检查 workline.contract_version", "检查插件 contract_version 与配置是否一致"],
    ),
    ErrorCode.DEVICE_UNREACHABLE: (
        ErrorDomain.DEVICE,
        Severity.ERROR,
        Recoverability.MANUAL_RETRYABLE,
        OwnerRole.HARDWARE_ENGINEER,
        "设备当前不可达，请检查网络、电源或设备服务。",
        ["检查设备网络连通性", "检查设备服务进程与端口状态"],
    ),
    ErrorCode.DEVICE_TIMEOUT: (
        ErrorDomain.NETWORK,
        Severity.ERROR,
        Recoverability.MANUAL_RETRYABLE,
        OwnerRole.HARDWARE_ENGINEER,
        "设备响应超时，请检查设备状态和通信链路。",
        ["检查设备响应耗时", "检查 timeout 配置是否合理"],
    ),
    ErrorCode.OUTBOX_DISPATCH_FAILED: (
        ErrorDomain.INTEGRATION,
        Severity.ERROR,
        Recoverability.AUTO_RETRYABLE,
        OwnerRole.HARDWARE_ENGINEER,
        "系统向设备或外部系统派发失败。",
        ["检查派发目标配置", "检查最近 outbox 失败记录"],
    ),
    ErrorCode.CONFIG_INVALID: (
        ErrorDomain.CONFIG,
        Severity.ERROR,
        Recoverability.MANUAL_INTERVENTION_REQUIRED,
        OwnerRole.IMPLEMENTATION_ENGINEER,
        "配置不完整或不合法，请先修正配置再继续。",
        ["检查 Device / Workline 主数据配置", "校验插件绑定与通信配置"],
    ),
    ErrorCode.UNKNOWN: (
        ErrorDomain.SYSTEM,
        Severity.ERROR,
        Recoverability.MANUAL_INTERVENTION_REQUIRED,
        OwnerRole.BACKEND_ENGINEER,
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
) -> DiagnosticContext:
    """从运行时实体提取统一诊断上下文。"""

    return DiagnosticContext(
        request_id=request_id,
        correlation_id=correlation_id
        or getattr(inbox, "correlation_id", None)
        or getattr(session, "correlation_id", None),
        session_id=getattr(session, "id", None),
        inbox_id=getattr(inbox, "id", None),
        outbox_id=getattr(outbox, "id", None),
        command_code=getattr(command, "command_code", None),
        device_code=getattr(device, "device_code", None) or getattr(outbox, "target_code", None),
        workline_id=getattr(workline, "id", None) or getattr(session, "workline_id", None),
        workline_code=getattr(workline, "line_code", None),
        plugin_key=getattr(session, "plugin_key", None) or getattr(workline, "plugin_key", None),
        canonical_event_type=canonical_event_type,
        transition=transition,
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

    error_domain, severity, recoverability, owner_role, default_user_message, default_steps = _DEFAULTS.get(
        error_code,
        _DEFAULTS[ErrorCode.UNKNOWN],
    )
    return DiagnosticEvent(
        error_code=error_code,
        error_domain=error_domain,
        severity=severity,
        recoverability=recoverability,
        owner_role=owner_role,
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
        owner_role=event.owner_role,
        user_message=event.user_message or event.message,
        operator_action=event.operator_action,
        technical_summary=event.technical_summary,
        next_steps=list(event.next_steps),
        context=event.context,
    )


__all__ = ["build_diagnostic_card", "build_diagnostic_context", "build_diagnostic_event"]
