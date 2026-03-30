"""工作线插件模块入口。"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.workline_plugin_registry import (
        WORKLINE_PLUGIN_REGISTRY,
        WorklinePluginDefinition,
        get_workline_plugin_definition,
    )
    from src.workline_plugins.smt_classifier.event_handlers import (
        EventType,
        SmtClassifierEventHandler,
        generate_move_forward_command,
        generate_pick_and_put_command,
        smt_classifier_event_handler,
    )
    from src.workline_plugins.smt_classifier.plugin import (
        DeviceRoleRequirement,
        SmtClassifierCommandType,
        SmtClassifierDeviceRole,
        SmtClassifierEventType,
        SmtClassifierPlugin,
        SmtClassifierStage,
        smt_classifier_plugin,
    )
    from src.workline_plugins.smt_classifier.state_machine import (
        SmtClassifierStageMachine,
        SmtClassifierStageStatus,
        SmtClassifierStateMachine,
        SmtClassifierStatus,
    )

__all__ = [
    "WORKLINE_PLUGIN_REGISTRY",
    "DeviceRoleRequirement",
    "EventType",
    "SmtClassifierCommandType",
    "SmtClassifierDeviceRole",
    "SmtClassifierEventHandler",
    "SmtClassifierEventType",
    "SmtClassifierPlugin",
    "SmtClassifierStage",
    "SmtClassifierStageMachine",
    "SmtClassifierStageStatus",
    "SmtClassifierStateMachine",
    "SmtClassifierStatus",
    "WorklinePluginDefinition",
    "generate_move_forward_command",
    "generate_pick_and_put_command",
    "get_workline_plugin_definition",
    "smt_classifier_event_handler",
    "smt_classifier_plugin",
]

_EXPORT_SOURCES: dict[str, tuple[str, str]] = {
    "DeviceRoleRequirement": (
        "src.workline_plugins.smt_classifier.plugin",
        "DeviceRoleRequirement",
    ),
    "EventType": ("src.workline_plugins.smt_classifier.event_handlers", "EventType"),
    "SmtClassifierCommandType": (
        "src.workline_plugins.smt_classifier.plugin",
        "SmtClassifierCommandType",
    ),
    "SmtClassifierDeviceRole": (
        "src.workline_plugins.smt_classifier.plugin",
        "SmtClassifierDeviceRole",
    ),
    "SmtClassifierEventHandler": (
        "src.workline_plugins.smt_classifier.event_handlers",
        "SmtClassifierEventHandler",
    ),
    "SmtClassifierEventType": (
        "src.workline_plugins.smt_classifier.plugin",
        "SmtClassifierEventType",
    ),
    "SmtClassifierPlugin": (
        "src.workline_plugins.smt_classifier.plugin",
        "SmtClassifierPlugin",
    ),
    "SmtClassifierStageMachine": (
        "src.workline_plugins.smt_classifier.state_machine",
        "SmtClassifierStageMachine",
    ),
    "SmtClassifierStageStatus": (
        "src.workline_plugins.smt_classifier.state_machine",
        "SmtClassifierStageStatus",
    ),
    "SmtClassifierStage": ("src.workline_plugins.smt_classifier.plugin", "SmtClassifierStage"),
    "SmtClassifierStateMachine": (
        "src.workline_plugins.smt_classifier.state_machine",
        "SmtClassifierStateMachine",
    ),
    "SmtClassifierStatus": (
        "src.workline_plugins.smt_classifier.state_machine",
        "SmtClassifierStatus",
    ),
    "WORKLINE_PLUGIN_REGISTRY": (
        "src.workline_plugin_registry",
        "WORKLINE_PLUGIN_REGISTRY",
    ),
    "WorklinePluginDefinition": (
        "src.workline_plugin_registry",
        "WorklinePluginDefinition",
    ),
    "generate_move_forward_command": (
        "src.workline_plugins.smt_classifier.event_handlers",
        "generate_move_forward_command",
    ),
    "generate_pick_and_put_command": (
        "src.workline_plugins.smt_classifier.event_handlers",
        "generate_pick_and_put_command",
    ),
    "get_workline_plugin_definition": (
        "src.workline_plugin_registry",
        "get_workline_plugin_definition",
    ),
    "smt_classifier_event_handler": (
        "src.workline_plugins.smt_classifier.event_handlers",
        "smt_classifier_event_handler",
    ),
    "smt_classifier_plugin": (
        "src.workline_plugins.smt_classifier.plugin",
        "smt_classifier_plugin",
    ),
}


def __getattr__(name: str) -> Any:
    """按需解析导出，避免包导入时触发重型依赖链。"""

    if name not in _EXPORT_SOURCES:
        raise AttributeError(name)

    module_name, attr_name = _EXPORT_SOURCES[name]
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value
