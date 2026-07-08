"""Callback 域诊断码登记表 — wlr.diagnostics.registry 镜像。"""

from __future__ import annotations

from dataclasses import dataclass

from .codes import ErrorCode, Recoverability


@dataclass(frozen=True, slots=True)
class DiagnosticCodeDefinition:
    """标准诊断码定义。"""

    code: ErrorCode
    owner: str
    cause: str
    operator_action: str
    fix: str
    recoverability: Recoverability
    docs_anchor: str


_REGISTRY: dict[ErrorCode, DiagnosticCodeDefinition] = {
    ErrorCode.CALLBACK_SCHEMA_INVALID: DiagnosticCodeDefinition(
        code=ErrorCode.CALLBACK_SCHEMA_INVALID,
        owner="integration",
        cause="设备或第三方回调字段不符合 WES callback 协议。",
        operator_action="暂停该工位操作，联系技术人员。告知设备型号和最后一次操作内容。",
        fix="按 callback 协议修正顶层字段和 data 结构后重发。",
        recoverability=Recoverability.MANUAL_RETRYABLE,
        docs_anchor="CALLBACK_SCHEMA_INVALID",
    ),
    ErrorCode.SESSION_CONTEXT_MISSING: DiagnosticCodeDefinition(
        code=ErrorCode.SESSION_CONTEXT_MISSING,
        owner="workflow",
        cause="事件缺少继续推进会话所需的上下文。",
        operator_action="暂停该料盘操作，不要移动物料。携带料盘条码联系技术人员。",
        fix="检查 inbox 与 session 归属字段，必要时人工创建或恢复会话。",
        recoverability=Recoverability.MANUAL_INTERVENTION_REQUIRED,
        docs_anchor="SESSION_CONTEXT_MISSING",
    ),
    ErrorCode.SESSION_RESOLVE_FAILED: DiagnosticCodeDefinition(
        code=ErrorCode.SESSION_RESOLVE_FAILED,
        owner="workflow",
        cause="入口事件无法定位目标工作线会话。",
        operator_action="系统无法识别该料盘归属。请暂停该工位，保持料盘不动，携带条码联系技术人员。",
        fix="检查 business_key、trace_id、设备绑定和 SessionResolver 规则。",
        recoverability=Recoverability.MANUAL_INTERVENTION_REQUIRED,
        docs_anchor="SESSION_RESOLVE_FAILED",
    ),
    ErrorCode.PLUGIN_EXECUTION_FAILED: DiagnosticCodeDefinition(
        code=ErrorCode.PLUGIN_EXECUTION_FAILED,
        owner="plugin",
        cause="工作线插件处理事件时抛错或返回失败。",
        operator_action="业务处理异常。请勿移动料盘，联系技术人员确认问题后再继续操作。",
        fix="回放该 inbox 并检查插件日志、输入归一化和状态迁移。",
        recoverability=Recoverability.MANUAL_RETRYABLE,
        docs_anchor="PLUGIN_EXECUTION_FAILED",
    ),
    ErrorCode.PLUGIN_TRANSITION_INVALID: DiagnosticCodeDefinition(
        code=ErrorCode.PLUGIN_TRANSITION_INVALID,
        owner="plugin",
        cause="插件尝试执行当前状态不允许的迁移。",
        operator_action="流程状态异常，无法继续推进。请勿手动干预物料，联系技术人员处理后重试。",
        fix="核对 session 当前状态和插件 transition 定义。",
        recoverability=Recoverability.MANUAL_RETRYABLE,
        docs_anchor="PLUGIN_TRANSITION_INVALID",
    ),
    ErrorCode.CONTRACT_MISMATCH: DiagnosticCodeDefinition(
        code=ErrorCode.CONTRACT_MISMATCH,
        owner="integration",
        cause="设备、工作线或插件契约版本不一致。",
        operator_action="系统与设备版本不兼容，该工位暂停使用。立即通知技术人员，不要在此工位继续操作。",
        fix="对齐设备 profile、workline.contract_version 和插件 manifest。",
        recoverability=Recoverability.MANUAL_INTERVENTION_REQUIRED,
        docs_anchor="CONTRACT_MISMATCH",
    ),
    ErrorCode.DEVICE_UNREACHABLE: DiagnosticCodeDefinition(
        code=ErrorCode.DEVICE_UNREACHABLE,
        owner="device",
        cause="设备服务不可连接或主数据通信配置缺失。",
        operator_action="设备无法连接。检查设备电源和网络指示灯是否正常，按复位键重启后等待 30 秒。若仍无法连接，联系设备维护人员。",
        fix="检查设备电源、网络、host/port 和 callback_path 配置。",
        recoverability=Recoverability.MANUAL_RETRYABLE,
        docs_anchor="DEVICE_UNREACHABLE",
    ),
    ErrorCode.DEVICE_TIMEOUT: DiagnosticCodeDefinition(
        code=ErrorCode.DEVICE_TIMEOUT,
        owner="device",
        cause="设备未在约定时间内返回结果。",
        operator_action="设备未及时响应。查看设备状态指示灯，按复位键后等待重试。若设备已完成动作但未回报，人工确认完成后联系技术人员重启流程。",
        fix="检查设备执行状态，必要时人工完成或重试命令。",
        recoverability=Recoverability.MANUAL_RETRYABLE,
        docs_anchor="DEVICE_TIMEOUT",
    ),
    ErrorCode.OUTBOX_ACK_TIMEOUT: DiagnosticCodeDefinition(
        code=ErrorCode.OUTBOX_ACK_TIMEOUT,
        owner="network",
        cause="WES 派发 HTTP 请求后未在通信 ACK 窗口内收到 200 响应。",
        operator_action="系统会用同一 command_code 自动重试。若重试耗尽，请等待人工对账提示。",
        fix="检查设备网络、服务进程和 dispatch attempt 证据；重试耗尽后走通信 ACK 对账。",
        recoverability=Recoverability.AUTO_RETRYABLE,
        docs_anchor="OUTBOX_ACK_TIMEOUT",
    ),
    ErrorCode.CALLBACK_DEADLINE_EXPIRED: DiagnosticCodeDefinition(
        code=ErrorCode.CALLBACK_DEADLINE_EXPIRED,
        owner="runtime",
        cause="设备已 ACK，但未在业务完成窗口内回传 Callback，物理状态未知。",
        operator_action="停止对受影响设备继续投料，现场确认动作是否完成，然后执行人工对账解除隔离。",
        fix="读取 session reconciliation 字段和 late callback evidence，调用 resolve API 决议 completed/failed/cancelled。",
        recoverability=Recoverability.MANUAL_INTERVENTION_REQUIRED,
        docs_anchor="CALLBACK_DEADLINE_EXPIRED",
    ),
    ErrorCode.OUTBOX_DISPATCH_FAILED: DiagnosticCodeDefinition(
        code=ErrorCode.OUTBOX_DISPATCH_FAILED,
        owner="integration",
        cause="Outbox 派发重试耗尽，WES 无法确认设备是否接受了 command_code。",
        operator_action="不要继续投料。现场确认设备是否收到或执行该 command_code，再由授权人员解除对账隔离。",
        fix="保留 retry exhausted evidence，按通信 ACK 对账 resolve 后释放 parked outbox。",
        recoverability=Recoverability.MANUAL_INTERVENTION_REQUIRED,
        docs_anchor="OUTBOX_DISPATCH_FAILED",
    ),
    ErrorCode.INBOX_PROCESSING_TIMEOUT: DiagnosticCodeDefinition(
        code=ErrorCode.INBOX_PROCESSING_TIMEOUT,
        owner="runtime",
        cause="Inbox worker 在保护时间内没有完成本次消息处理。",
        operator_action="保持当前物料状态，联系技术人员检查 worker 日志。",
        fix="排查 worker 超时、锁等待或插件执行耗时，修复后重试或 replay。",
        recoverability=Recoverability.MANUAL_RETRYABLE,
        docs_anchor="INBOX_PROCESSING_TIMEOUT",
    ),
    ErrorCode.RESOURCE_WAIT: DiagnosticCodeDefinition(
        code=ErrorCode.RESOURCE_WAIT,
        owner="workflow",
        cause="Session 下一步所需的真实资源暂不可用，系统已进入自动等待重试。",
        operator_action="等待目标资源释放后系统自动重试；如长时间未恢复，请检查诊断证据中的资源状态。",
        fix="检查 subject_type、subject_key、projection_type、当前占用 Session 和现场 subject 释放事件，确认释放后观察下一轮重试。",
        recoverability=Recoverability.AUTO_RETRYABLE,
        docs_anchor="RESOURCE_WAIT",
    ),
    ErrorCode.INBOX_RETRY_EXHAUSTED: DiagnosticCodeDefinition(
        code=ErrorCode.INBOX_RETRY_EXHAUSTED,
        owner="workflow",
        cause="Inbox 多次处理失败并进入死信。",
        operator_action="该事件已多次处理失败并停止。保持料盘不动，携带条码联系技术人员进行人工重放处理。",
        fix="查看诊断卡 evidence，修复根因后通过 replay 创建新事件。",
        recoverability=Recoverability.MANUAL_INTERVENTION_REQUIRED,
        docs_anchor="INBOX_RETRY_EXHAUSTED",
    ),
    ErrorCode.WMS_TIMEOUT: DiagnosticCodeDefinition(
        code=ErrorCode.WMS_TIMEOUT,
        owner="integration",
        cause="WES 已完成前序设备链路，但调用 WMS 库存或同步接口超时。",
        operator_action="保持当前物料状态，人工检查粗分机当前物料与 WMS 库存依赖状态。",
        fix="检查 WMS_INVENTORY 请求、网络链路和 WMS 服务日志，确认后重新触发集成测试。",
        recoverability=Recoverability.MANUAL_INTERVENTION_REQUIRED,
        docs_anchor="WMS_TIMEOUT",
    ),
    ErrorCode.CONFIG_INVALID: DiagnosticCodeDefinition(
        code=ErrorCode.CONFIG_INVALID,
        owner="configuration",
        cause="运行所需设备、工作线或插件配置不完整。",
        operator_action="该工位配置不完整，无法正常运行。联系运维人员修复配置后方可继续使用。",
        fix="修正主数据配置并重新触发事件。",
        recoverability=Recoverability.MANUAL_INTERVENTION_REQUIRED,
        docs_anchor="CONFIG_INVALID",
    ),
    ErrorCode.UNKNOWN: DiagnosticCodeDefinition(
        code=ErrorCode.UNKNOWN,
        owner="platform",
        cause="当前证据不足，无法归入已知诊断码。",
        operator_action="发生未知系统错误。记录料盘条码和当前时间，联系技术支持，并提供页面显示的诊断码。",
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
