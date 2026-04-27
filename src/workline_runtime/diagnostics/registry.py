"""诊断码登记表。

登记表是诊断卡面向现场人员和集成方的稳定解释层：
- owner：默认责任边界
- cause：常见根因
- fix：优先恢复动作
- recoverability：默认恢复方式
"""

from __future__ import annotations

from dataclasses import dataclass

from .codes import ErrorCode, Recoverability


@dataclass(frozen=True, slots=True)
class DiagnosticCodeDefinition:
    """标准诊断码定义。"""

    code: ErrorCode
    owner: str
    cause: str
    fix: str
    recoverability: Recoverability
    docs_anchor: str


_REGISTRY: dict[ErrorCode, DiagnosticCodeDefinition] = {
    ErrorCode.CALLBACK_SCHEMA_INVALID: DiagnosticCodeDefinition(
        code=ErrorCode.CALLBACK_SCHEMA_INVALID,
        owner="integration",
        cause="设备或第三方回调字段不符合 WES callback 协议。",
        fix="按 callback 协议修正顶层字段和 data 结构后重发。",
        recoverability=Recoverability.MANUAL_RETRYABLE,
        docs_anchor="CALLBACK_SCHEMA_INVALID",
    ),
    ErrorCode.SESSION_CONTEXT_MISSING: DiagnosticCodeDefinition(
        code=ErrorCode.SESSION_CONTEXT_MISSING,
        owner="workflow",
        cause="事件缺少继续推进会话所需的上下文。",
        fix="检查 inbox 与 session 归属字段，必要时人工创建或恢复会话。",
        recoverability=Recoverability.MANUAL_INTERVENTION_REQUIRED,
        docs_anchor="SESSION_CONTEXT_MISSING",
    ),
    ErrorCode.SESSION_RESOLVE_FAILED: DiagnosticCodeDefinition(
        code=ErrorCode.SESSION_RESOLVE_FAILED,
        owner="workflow",
        cause="入口事件无法定位目标工作线会话。",
        fix="检查 business_key、trace_id、设备绑定和 SessionResolver 规则。",
        recoverability=Recoverability.MANUAL_INTERVENTION_REQUIRED,
        docs_anchor="SESSION_RESOLVE_FAILED",
    ),
    ErrorCode.PLUGIN_EXECUTION_FAILED: DiagnosticCodeDefinition(
        code=ErrorCode.PLUGIN_EXECUTION_FAILED,
        owner="plugin",
        cause="工作线插件处理事件时抛错或返回失败。",
        fix="回放该 inbox 并检查插件日志、输入归一化和状态迁移。",
        recoverability=Recoverability.MANUAL_RETRYABLE,
        docs_anchor="PLUGIN_EXECUTION_FAILED",
    ),
    ErrorCode.PLUGIN_TRANSITION_INVALID: DiagnosticCodeDefinition(
        code=ErrorCode.PLUGIN_TRANSITION_INVALID,
        owner="plugin",
        cause="插件尝试执行当前状态不允许的迁移。",
        fix="核对 session 当前状态和插件 transition 定义。",
        recoverability=Recoverability.MANUAL_RETRYABLE,
        docs_anchor="PLUGIN_TRANSITION_INVALID",
    ),
    ErrorCode.CONTRACT_MISMATCH: DiagnosticCodeDefinition(
        code=ErrorCode.CONTRACT_MISMATCH,
        owner="integration",
        cause="设备、工作线或插件契约版本不一致。",
        fix="对齐设备 profile、workline.contract_version 和插件 manifest。",
        recoverability=Recoverability.MANUAL_INTERVENTION_REQUIRED,
        docs_anchor="CONTRACT_MISMATCH",
    ),
    ErrorCode.DEVICE_UNREACHABLE: DiagnosticCodeDefinition(
        code=ErrorCode.DEVICE_UNREACHABLE,
        owner="device",
        cause="设备服务不可连接或主数据通信配置缺失。",
        fix="检查设备电源、网络、host/port 和 callback_path 配置。",
        recoverability=Recoverability.MANUAL_RETRYABLE,
        docs_anchor="DEVICE_UNREACHABLE",
    ),
    ErrorCode.DEVICE_TIMEOUT: DiagnosticCodeDefinition(
        code=ErrorCode.DEVICE_TIMEOUT,
        owner="device",
        cause="设备未在约定时间内返回结果。",
        fix="检查设备执行状态，必要时人工完成或重试命令。",
        recoverability=Recoverability.MANUAL_RETRYABLE,
        docs_anchor="DEVICE_TIMEOUT",
    ),
    ErrorCode.OUTBOX_DISPATCH_FAILED: DiagnosticCodeDefinition(
        code=ErrorCode.OUTBOX_DISPATCH_FAILED,
        owner="integration",
        cause="Outbox 对设备或外部系统的派发未成功。",
        fix="检查目标配置、最近一次 attempt 错误和外部服务可用性后重试。",
        recoverability=Recoverability.AUTO_RETRYABLE,
        docs_anchor="OUTBOX_DISPATCH_FAILED",
    ),
    ErrorCode.INBOX_RETRY_EXHAUSTED: DiagnosticCodeDefinition(
        code=ErrorCode.INBOX_RETRY_EXHAUSTED,
        owner="workflow",
        cause="Inbox 多次处理失败并进入死信。",
        fix="查看诊断卡 evidence，修复根因后通过 replay 创建新事件。",
        recoverability=Recoverability.MANUAL_INTERVENTION_REQUIRED,
        docs_anchor="INBOX_RETRY_EXHAUSTED",
    ),
    ErrorCode.CONFIG_INVALID: DiagnosticCodeDefinition(
        code=ErrorCode.CONFIG_INVALID,
        owner="configuration",
        cause="运行所需设备、工作线或插件配置不完整。",
        fix="修正主数据配置并重新触发事件。",
        recoverability=Recoverability.MANUAL_INTERVENTION_REQUIRED,
        docs_anchor="CONFIG_INVALID",
    ),
    ErrorCode.UNKNOWN: DiagnosticCodeDefinition(
        code=ErrorCode.UNKNOWN,
        owner="platform",
        cause="当前证据不足，无法归入已知诊断码。",
        fix="补充 callback、inbox、timeline 和 outbox evidence 后重新诊断。",
        recoverability=Recoverability.MANUAL_INTERVENTION_REQUIRED,
        docs_anchor="UNKNOWN",
    ),
}


def get_diagnostic_code_definition(error_code: ErrorCode) -> DiagnosticCodeDefinition:
    """返回标准诊断码定义。"""

    return _REGISTRY.get(error_code, _REGISTRY[ErrorCode.UNKNOWN])


def list_diagnostic_code_definitions() -> list[DiagnosticCodeDefinition]:
    """列出所有标准诊断码定义。"""

    return list(_REGISTRY.values())


__all__ = [
    "DiagnosticCodeDefinition",
    "get_diagnostic_code_definition",
    "list_diagnostic_code_definitions",
]
