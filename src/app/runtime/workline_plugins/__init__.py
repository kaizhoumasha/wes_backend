"""Workline 插件 Definition 与决策公共合同。"""

from .attempt_coordinator import (
    AttemptCoordinator,
    AttemptSnapshot,
    AttemptWriteSet,
    PluginAttemptContext,
    PluginAttemptRunner,
    PluginWriteSetLimits,
    UnavailablePluginAttemptRunner,
    WriteDisposition,
    bound_attempt_write_set,
)
from .contracts import MAX_PLUGIN_DECISION_INTENTS, PluginContext, PluginDecision
from .definition import WorklinePluginDefinition
from .dispatcher import (
    HandlerRegistration,
    PinnedPluginSnapshot,
    PluginDispatchRequest,
    WorklinePluginDispatcher,
)

__all__ = [
    "MAX_PLUGIN_DECISION_INTENTS",
    "AttemptCoordinator",
    "AttemptSnapshot",
    "AttemptWriteSet",
    "HandlerRegistration",
    "PinnedPluginSnapshot",
    "PluginAttemptContext",
    "PluginAttemptRunner",
    "PluginContext",
    "PluginDecision",
    "PluginDispatchRequest",
    "PluginWriteSetLimits",
    "UnavailablePluginAttemptRunner",
    "WorklinePluginDefinition",
    "WorklinePluginDispatcher",
    "WriteDisposition",
    "bound_attempt_write_set",
]
