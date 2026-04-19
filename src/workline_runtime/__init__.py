"""
WES 作业线运行时模块

本模块提供作业线插件化编排的核心基础设施：
- 分布式锁（RedisDistributedLock）
- 插件类型定义（PluginResult, WaitIntent, CommandIntent, FailureIntent）
- 插件上下文（PluginContext）
- 默认插件（NullPlugin）
- 统一输入模型（Inbox）
- 副作用派发（Outbox）
- 会话管理（Session）
- 时间线追踪（Timeline）
- 决策记录（Decision）
- 外部调用日志（ExternalCall）
- 枚举定义（SessionStatus, FailureDomain 等）
- 统一诊断模型与软件/硬件归类
- 插件 SDK 标准化输入与运行时快照

设计原则：
- DRY: 横切能力统一实现，不散落到插件中
- KISS: 使用 Python 类和显式状态机，避免复杂 DSL
- SOLID: 分层职责清晰，插件不直接操作基础设施
- YAGNI: 仅实现当前业务明确需要的能力
"""

from src.app.workline.models.inbox import InboxStatus
from src.app.workline.models.session import SessionStatus
from src.workline_runtime.atomic_writer import AtomicWriter, atomic_writer
from src.workline_runtime.diagnostics import (
    DiagnosticCard,
    DiagnosticContext,
    DiagnosticEvent,
    ErrorCode,
    ErrorDomain,
    ProblemClass,
    Recoverability,
    Severity,
    build_diagnostic_card,
    build_diagnostic_context,
    build_diagnostic_event,
)
from src.workline_runtime.enums import (
    DecisionType,
    FailureDomain,
    ManualOperationType,
    OutboxDispatchType,
    OutboxStatus,
    TimelineStage,
)
from src.workline_runtime.lock import LockAcquireError, LockReleaseError, RedisDistributedLock
from src.workline_runtime.null_plugin import NullPlugin, null_plugin
from src.workline_runtime.orchestrator import OrchestratorResult, OrchestratorService
from src.workline_runtime.plugin_context import PluginContext, PluginContextBuilder
from src.workline_runtime.plugin_sdk import (
    NormalizedCommandResult,
    NormalizedDeviceEvent,
    NormalizedExternalCallback,
    ResolvedDeviceRuntimeConfig,
    ResolvedExecutionContext,
    ResolvedWorklineRuntimeConfig,
    canonicalize_event_type,
    classify_result,
    normalize_inbox_input,
    resolve_device_runtime_config,
    resolve_execution_context,
    resolve_workline_runtime_config,
)
from src.workline_runtime.session_resolver import SessionResolver, session_resolver
from src.workline_runtime.trace_context import TraceContext
from src.workline_runtime.transition_validator import TransitionValidator
from src.workline_runtime.types import CommandIntent, FailureIntent, PluginResult, WaitIntent

__version__ = "1.0.0"

__all__ = [
    "AtomicWriter",
    "CommandIntent",
    "DecisionType",
    "DiagnosticCard",
    "DiagnosticContext",
    "DiagnosticEvent",
    "ErrorCode",
    "ErrorDomain",
    "FailureDomain",
    "FailureIntent",
    "InboxStatus",
    "LockAcquireError",
    "LockReleaseError",
    "ManualOperationType",
    "NormalizedCommandResult",
    "NormalizedDeviceEvent",
    "NormalizedExternalCallback",
    "NullPlugin",
    "OrchestratorResult",
    "OrchestratorService",
    "OutboxDispatchType",
    "OutboxStatus",
    "PluginContext",
    "PluginContextBuilder",
    "PluginResult",
    "ProblemClass",
    "Recoverability",
    "RedisDistributedLock",
    "ResolvedDeviceRuntimeConfig",
    "ResolvedExecutionContext",
    "ResolvedWorklineRuntimeConfig",
    "SessionResolver",
    "SessionStatus",
    "Severity",
    "TimelineStage",
    "TraceContext",
    "TransitionValidator",
    "WaitIntent",
    "atomic_writer",
    "build_diagnostic_card",
    "build_diagnostic_context",
    "build_diagnostic_event",
    "canonicalize_event_type",
    "classify_result",
    "normalize_inbox_input",
    "null_plugin",
    "resolve_device_runtime_config",
    "resolve_execution_context",
    "resolve_workline_runtime_config",
    "session_resolver",
]
