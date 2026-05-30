"""统一诊断编码定义。

开发约定：
1. 优先选择最贴近根因的错误码，不要一律落到 ``UNKNOWN``。
2. 先判断问题边界，再判断失败形态：
   - 边界：优先看 ``ErrorDomain``
   - 失败形态：优先看 ``ErrorCode``
3. 当同一次失败可以套用多个错误码时，选择更便于排障和归责的那个。
"""

from enum import Enum


class ErrorDomain(str, Enum):
    """诊断错误域。

    用于回答“问题首先落在哪个边界”：
    - 设备连不上、设备超时：``DEVICE``
    - 插件抛错、插件状态推进错误：``PLUGIN``
    - Session / 工作流推进失败：``WORKFLOW``
    - 外部系统契约或集成链路问题：``INTEGRATION``
    - 纯网络链路故障：``NETWORK``
    - 输入数据本身有问题：``DATA_QUALITY``
    - 配置错误：``CONFIG``
    - 其他系统级兜底：``SYSTEM``
    """

    DEVICE = "DEVICE"  # 设备侧问题，如设备不可达、设备超时等。
    PLUGIN = "PLUGIN"  # 插件执行或插件状态迁移相关问题。
    WORKFLOW = "WORKFLOW"  # 作业流上下文、会话推进或流程编排问题。
    INTEGRATION = "INTEGRATION"  # 外部系统集成、契约对接或回调适配问题。
    NETWORK = "NETWORK"  # 网络连接、请求链路或通信超时问题。
    DATA_QUALITY = "DATA_QUALITY"  # 输入数据缺失、格式异常或数据质量问题。
    CONFIG = "CONFIG"  # 配置缺失、配置非法或环境配置不一致问题。
    SYSTEM = "SYSTEM"  # 无法归类到具体业务域的系统级问题。


class ErrorCode(str, Enum):
    """跨角色共享的标准错误码。

    选码速查：
    - 回调 payload 结构不对：``CALLBACK_SCHEMA_INVALID``
    - 缺少当前流程继续执行所需上下文：``SESSION_CONTEXT_MISSING``
    - 有输入但仍无法定位 Session：``SESSION_RESOLVE_FAILED``
    - 插件内部报错：``PLUGIN_EXECUTION_FAILED``
    - 插件返回了不合法状态迁移：``PLUGIN_TRANSITION_INVALID``
    - 对接字段/版本/协议不一致：``CONTRACT_MISMATCH``
    - 设备当前连不上：``DEVICE_UNREACHABLE``
    - 派发 ACK 通信超时：``OUTBOX_ACK_TIMEOUT``
    - 执行 Callback 超时：``CALLBACK_DEADLINE_EXPIRED``
    - 指令派发动作失败：``OUTBOX_DISPATCH_FAILED``
    - Inbox worker 自身处理超时：``INBOX_PROCESSING_TIMEOUT``
    - 重试次数耗尽：``INBOX_RETRY_EXHAUSTED``
    - 配置本身有问题：``CONFIG_INVALID``
    - 无法明确归因时才使用：``UNKNOWN``
    """

    # 回调 / 契约输入问题
    CALLBACK_SCHEMA_INVALID = "CALLBACK_SCHEMA_INVALID"  # 回调载荷不符合预期 Schema。
    CONTRACT_MISMATCH = "CONTRACT_MISMATCH"  # 上下游契约版本或字段约定不匹配。

    # Session / 工作流上下文问题
    SESSION_CONTEXT_MISSING = "SESSION_CONTEXT_MISSING"  # 缺少继续处理所需的 Session 上下文。
    SESSION_RESOLVE_FAILED = "SESSION_RESOLVE_FAILED"  # 无法根据输入解析到目标 Session。

    # 插件执行与状态迁移问题
    PLUGIN_EXECUTION_FAILED = "PLUGIN_EXECUTION_FAILED"  # 插件执行过程中抛出异常或返回失败。
    PLUGIN_TRANSITION_INVALID = "PLUGIN_TRANSITION_INVALID"  # 插件尝试了不合法的状态迁移。

    # 设备与消息派发问题
    DEVICE_UNREACHABLE = "DEVICE_UNREACHABLE"  # 目标设备不可连接或当前不可达。
    DEVICE_TIMEOUT = "DEVICE_TIMEOUT"  # 设备在约定时间内未返回结果。
    OUTBOX_ACK_TIMEOUT = "OUTBOX_ACK_TIMEOUT"  # HTTP 派发未在通信 ACK 窗口内返回 200。
    CALLBACK_DEADLINE_EXPIRED = "CALLBACK_DEADLINE_EXPIRED"  # ACK 后执行 Callback 未在业务窗口内回传。
    OUTBOX_DISPATCH_FAILED = "OUTBOX_DISPATCH_FAILED"  # Outbox 派发失败，消息未成功下发。
    INBOX_PROCESSING_TIMEOUT = "INBOX_PROCESSING_TIMEOUT"  # Inbox worker 自身处理超时。

    # 重试 / 配置 / 兜底问题
    INBOX_RETRY_EXHAUSTED = "INBOX_RETRY_EXHAUSTED"  # Inbox 重试次数耗尽，仍未处理成功。
    WMS_TIMEOUT = "WMS_TIMEOUT"  # 外部 WMS 同步或查询调用超时。
    CONFIG_INVALID = "CONFIG_INVALID"  # 运行所需配置非法、缺失或彼此冲突。
    UNKNOWN = "UNKNOWN"  # 暂未识别根因的兜底错误码。


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
    ErrorCode.INBOX_RETRY_EXHAUSTED: ErrorDomain.SYSTEM,
    ErrorCode.WMS_TIMEOUT: ErrorDomain.INTEGRATION,
    ErrorCode.CONFIG_INVALID: ErrorDomain.CONFIG,
    ErrorCode.UNKNOWN: ErrorDomain.SYSTEM,
}


def error_domain_for(error_code: ErrorCode) -> ErrorDomain:
    """根据错误码返回默认错误域。"""

    return _ERROR_CODE_TO_DOMAIN.get(error_code, ErrorDomain.SYSTEM)


class Severity(str, Enum):
    """诊断严重度。

    用于回答“这件事有多严重”：
    - ``INFO``: 记录信息，不代表失败
    - ``WARNING``: 有异常征兆，但流程可能继续
    - ``ERROR``: 当前步骤已失败
    - ``CRITICAL``: 影响核心链路，通常需要立即处理
    """

    INFO = "info"  # 信息级，记录状态或上下文，不表示异常。
    WARNING = "warning"  # 警告级，存在风险或轻微异常，但流程可能继续。
    ERROR = "error"  # 错误级，当前步骤失败，需要处理或重试。
    CRITICAL = "critical"  # 严重级，影响核心流程，通常需要立即介入。


class Recoverability(str, Enum):
    """诊断可恢复性。

    用于回答“接下来应该怎么恢复”：
    - ``AUTO_RETRYABLE``: 系统自己重试
    - ``MANUAL_RETRYABLE``: 人工触发重试
    - ``MANUAL_INTERVENTION_REQUIRED``: 需要现场或业务人工介入
    - ``NON_RECOVERABLE``: 当前流程直接终止
    """

    AUTO_RETRYABLE = "auto_retryable"  # 系统可自动重试并有机会自行恢复。
    MANUAL_RETRYABLE = "manual_retryable"  # 需要人工触发重试，但重试仍可能成功。
    MANUAL_INTERVENTION_REQUIRED = "manual_intervention_required"  # 必须人工介入处理现场或业务状态。
    NON_RECOVERABLE = "non_recoverable"  # 当前上下文下不可恢复，需要终止或改走其他流程。


class ProblemClass(str, Enum):
    """问题归属大类。

    用于回答“这是软问题还是硬问题”：
    - ``SOFTWARE``: 软件、配置、编排、集成逻辑问题
    - ``HARDWARE``: 设备、传感器、执行机构等硬件问题
    """

    SOFTWARE = "software"  # 软件、配置、编排或集成逻辑问题。
    HARDWARE = "hardware"  # 设备、传感器、执行机构等硬件问题。


__all__ = [
    "ErrorCode",
    "ErrorDomain",
    "ProblemClass",
    "Recoverability",
    "Severity",
    "error_domain_for",
]
