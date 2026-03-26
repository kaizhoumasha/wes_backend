"""SMT 粗分机插件模块入口。"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.workline_plugins.smt_classifier.event_handlers import (
        CommandResult,
        CommandResultData,
        ErrorDetail,
        EventType,
        LocationInfo,
        LocationType,
        SmtClassifierEventHandler,
        TaskType,
        generate_move_forward_command,
        generate_pick_and_put_command,
        smt_classifier_event_handler,
    )
    from src.workline_plugins.smt_classifier.plugin import (
        DeviceRoleRequirement,
        SmtClassifierCommandType,
        SmtClassifierDeviceRole,
        SmtClassifierEventType,
        SmtClassifierLocationId,
        SmtClassifierPlugin,
        SmtClassifierStage,
        smt_classifier_plugin,
    )
    from src.workline_plugins.smt_classifier.state_machine import (
        STATES,
        TRANSITIONS,
        SmtClassifierStageMachine,
        SmtClassifierStageStatus,
        SmtClassifierStateMachine,
        SmtClassifierStatus,
        get_valid_stage_transitions,
        get_valid_transitions,
    )

__all__ = [
    "STATES",
    "TRANSITIONS",
    "CommandResult",
    "CommandResultData",
    "DeviceRoleRequirement",
    "ErrorDetail",
    "EventType",
    "LocationInfo",
    "LocationType",
    "SmtClassifierCommandType",
    "SmtClassifierDeviceRole",
    "SmtClassifierEventHandler",
    "SmtClassifierEventType",
    "SmtClassifierLocationId",
    "SmtClassifierPlugin",
    "SmtClassifierStage",
    "SmtClassifierStageMachine",
    "SmtClassifierStageStatus",
    "SmtClassifierStateMachine",
    "SmtClassifierStatus",
    "TaskType",
    "generate_move_forward_command",
    "generate_pick_and_put_command",
    "get_valid_stage_transitions",
    "get_valid_transitions",
    "smt_classifier_event_handler",
    "smt_classifier_plugin",
]

_EXPORT_SOURCES: dict[str, tuple[str, str]] = {
    "CommandResult": ("src.workline_plugins.smt_classifier.event_handlers", "CommandResult"),
    "CommandResultData": ("src.workline_plugins.smt_classifier.event_handlers", "CommandResultData"),
    "DeviceRoleRequirement": ("src.workline_plugins.smt_classifier.plugin", "DeviceRoleRequirement"),
    "ErrorDetail": ("src.workline_plugins.smt_classifier.event_handlers", "ErrorDetail"),
    "EventType": ("src.workline_plugins.smt_classifier.event_handlers", "EventType"),
    "LocationInfo": ("src.workline_plugins.smt_classifier.event_handlers", "LocationInfo"),
    "LocationType": ("src.workline_plugins.smt_classifier.event_handlers", "LocationType"),
    "STATES": ("src.workline_plugins.smt_classifier.state_machine", "STATES"),
    "SmtClassifierCommandType": ("src.workline_plugins.smt_classifier.plugin", "SmtClassifierCommandType"),
    "SmtClassifierDeviceRole": ("src.workline_plugins.smt_classifier.plugin", "SmtClassifierDeviceRole"),
    "SmtClassifierEventHandler": (
        "src.workline_plugins.smt_classifier.event_handlers",
        "SmtClassifierEventHandler",
    ),
    "SmtClassifierEventType": ("src.workline_plugins.smt_classifier.plugin", "SmtClassifierEventType"),
    "SmtClassifierLocationId": ("src.workline_plugins.smt_classifier.plugin", "SmtClassifierLocationId"),
    "SmtClassifierPlugin": ("src.workline_plugins.smt_classifier.plugin", "SmtClassifierPlugin"),
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
    "SmtClassifierStatus": ("src.workline_plugins.smt_classifier.state_machine", "SmtClassifierStatus"),
    "TRANSITIONS": ("src.workline_plugins.smt_classifier.state_machine", "TRANSITIONS"),
    "TaskType": ("src.workline_plugins.smt_classifier.event_handlers", "TaskType"),
    "generate_move_forward_command": (
        "src.workline_plugins.smt_classifier.event_handlers",
        "generate_move_forward_command",
    ),
    "generate_pick_and_put_command": (
        "src.workline_plugins.smt_classifier.event_handlers",
        "generate_pick_and_put_command",
    ),
    "get_valid_transitions": (
        "src.workline_plugins.smt_classifier.state_machine",
        "get_valid_transitions",
    ),
    "get_valid_stage_transitions": (
        "src.workline_plugins.smt_classifier.state_machine",
        "get_valid_stage_transitions",
    ),
    "smt_classifier_event_handler": (
        "src.workline_plugins.smt_classifier.event_handlers",
        "smt_classifier_event_handler",
    ),
    "smt_classifier_plugin": ("src.workline_plugins.smt_classifier.plugin", "smt_classifier_plugin"),
}


def __getattr__(name: str) -> Any:
    """按需解析导出，避免包导入时触发重型依赖链。"""

    if name not in _EXPORT_SOURCES:
        raise AttributeError(name)

    module_name, attr_name = _EXPORT_SOURCES[name]
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value
