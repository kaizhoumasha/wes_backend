"""统一诊断编码定义。"""

from enum import Enum


class ErrorDomain(str, Enum):
    """诊断错误域。"""

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
    SESSION_CONTEXT_MISSING = "SESSION_CONTEXT_MISSING"
    SESSION_RESOLVE_FAILED = "SESSION_RESOLVE_FAILED"
    PLUGIN_EXECUTION_FAILED = "PLUGIN_EXECUTION_FAILED"
    PLUGIN_TRANSITION_INVALID = "PLUGIN_TRANSITION_INVALID"
    CONTRACT_MISMATCH = "CONTRACT_MISMATCH"
    DEVICE_UNREACHABLE = "DEVICE_UNREACHABLE"
    DEVICE_TIMEOUT = "DEVICE_TIMEOUT"
    OUTBOX_DISPATCH_FAILED = "OUTBOX_DISPATCH_FAILED"
    INBOX_RETRY_EXHAUSTED = "INBOX_RETRY_EXHAUSTED"
    CONFIG_INVALID = "CONFIG_INVALID"
    UNKNOWN = "UNKNOWN"


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
]
