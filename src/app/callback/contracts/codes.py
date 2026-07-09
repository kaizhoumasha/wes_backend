"""Callback 域诊断错误码 — legacy runtime.diagnostics.codes 镜像。

镜像说明:
- ErrorCode 枚举值与 legacy runtime.diagnostics.codes 完全一致,跨域调用方按字符串
  比对 (例如 runtime_query_service 输出 DiagnosticCard.error_code.value),
  镜像保留兼容。
- _ERROR_CODE_TO_DOMAIN 映射与 legacy runtime 一致,error_domain_for() 行为不变。
"""

from enum import Enum


class ErrorDomain(str, Enum):
    """诊断错误域。

    用于回答"问题首先落在哪个边界":
    - 设备连不上、设备超时:``DEVICE``
    - 插件抛错、插件状态推进错误:``PLUGIN``
    - Session / 工作流推进失败:``WORKFLOW``
    - 外部系统契约或集成链路问题:``INTEGRATION``
    - 纯网络链路故障:``NETWORK``
    - 输入数据本身有问题:``DATA_QUALITY``
    - 配置错误:``CONFIG``
    - 其他系统级兜底:``SYSTEM``
    """

    DEVICE = "DEVICE"
    PLUGIN = "PLUGIN"
    WORKFLOW = "WORKFLOW"
    INTEGRATION = "INTEGRATION"
    NETWORK = "NETWORK"
    DATA_QUALITY = "DATA_QUALITY"
    CONFIG = "CONFIG"
    SYSTEM = "SYSTEM"


class ErrorCode(str, Enum):
    """跨角色共享的标准错误码。"""

    CALLBACK_SCHEMA_INVALID = "CALLBACK_SCHEMA_INVALID"
    CONTRACT_MISMATCH = "CONTRACT_MISMATCH"
    SESSION_CONTEXT_MISSING = "SESSION_CONTEXT_MISSING"
    SESSION_RESOLVE_FAILED = "SESSION_RESOLVE_FAILED"
    PLUGIN_EXECUTION_FAILED = "PLUGIN_EXECUTION_FAILED"
    PLUGIN_TRANSITION_INVALID = "PLUGIN_TRANSITION_INVALID"
    DEVICE_UNREACHABLE = "DEVICE_UNREACHABLE"
    DEVICE_TIMEOUT = "DEVICE_TIMEOUT"
    OUTBOX_ACK_TIMEOUT = "OUTBOX_ACK_TIMEOUT"
    CALLBACK_DEADLINE_EXPIRED = "CALLBACK_DEADLINE_EXPIRED"
    OUTBOX_DISPATCH_FAILED = "OUTBOX_DISPATCH_FAILED"
    INBOX_PROCESSING_TIMEOUT = "INBOX_PROCESSING_TIMEOUT"
    RESOURCE_WAIT = "RESOURCE_WAIT"
    INBOX_RETRY_EXHAUSTED = "INBOX_RETRY_EXHAUSTED"
    WMS_TIMEOUT = "WMS_TIMEOUT"
    CONFIG_INVALID = "CONFIG_INVALID"
    UNKNOWN = "UNKNOWN"


_ERROR_CODE_TO_DOMAIN: dict[ErrorCode, ErrorDomain] = {
    ErrorCode.CALLBACK_SCHEMA_INVALID: ErrorDomain.DATA_QUALITY,
    ErrorCode.CONTRACT_MISMATCH: ErrorDomain.CONFIG,
    ErrorCode.SESSION_CONTEXT_MISSING: ErrorDomain.WORKFLOW,
    ErrorCode.SESSION_RESOLVE_FAILED: ErrorDomain.WORKFLOW,
    ErrorCode.PLUGIN_EXECUTION_FAILED: ErrorDomain.PLUGIN,
    ErrorCode.PLUGIN_TRANSITION_INVALID: ErrorDomain.PLUGIN,
    ErrorCode.DEVICE_UNREACHABLE: ErrorDomain.DEVICE,
    ErrorCode.DEVICE_TIMEOUT: ErrorDomain.NETWORK,
    ErrorCode.OUTBOX_ACK_TIMEOUT: ErrorDomain.NETWORK,
    ErrorCode.CALLBACK_DEADLINE_EXPIRED: ErrorDomain.WORKFLOW,
    ErrorCode.OUTBOX_DISPATCH_FAILED: ErrorDomain.INTEGRATION,
    ErrorCode.INBOX_PROCESSING_TIMEOUT: ErrorDomain.SYSTEM,
    ErrorCode.RESOURCE_WAIT: ErrorDomain.WORKFLOW,
    ErrorCode.INBOX_RETRY_EXHAUSTED: ErrorDomain.SYSTEM,
    ErrorCode.WMS_TIMEOUT: ErrorDomain.INTEGRATION,
    ErrorCode.CONFIG_INVALID: ErrorDomain.CONFIG,
    ErrorCode.UNKNOWN: ErrorDomain.SYSTEM,
}


def error_domain_for(error_code: ErrorCode) -> ErrorDomain:
    """根据错误码返回默认错误域。"""
    return _ERROR_CODE_TO_DOMAIN.get(error_code, ErrorDomain.SYSTEM)


class Severity(str, Enum):
    """诊断严重度。"""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class Recoverability(str, Enum):
    """诊断可恢复性。"""

    AUTO_RETRYABLE = "auto_retryable"
    MANUAL_RETRYABLE = "manual_retryable"
    MANUAL_INTERVENTION_REQUIRED = "manual_intervention_required"
    NON_RECOVERABLE = "non_recoverable"


class ProblemClass(str, Enum):
    """问题归属大类。"""

    SOFTWARE = "software"
    HARDWARE = "hardware"


__all__ = [
    "ErrorCode",
    "ErrorDomain",
    "ProblemClass",
    "Recoverability",
    "Severity",
    "error_domain_for",
]
