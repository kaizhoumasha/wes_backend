"""SMT 粗分机插件模块入口。"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.workline_plugins.smt_classifier.contract import (
        BARCODE_LIST_KEY,
        COMMAND_CODE_ALIASES,
        COMMAND_FIELD_RULES,
        CONTRACT_VERSION,
        DEVICE_CODE_ALIASES,
        EVENT_FIELD_ALIASES,
        RESULT_FIELD_ALIASES,
        STEP_CODE_KEY,
        SmtClassifierCommandType,
        SmtClassifierEventType,
        SmtClassifierResultType,
        SmtClassifierStepCode,
        ensure_dict,
        infer_step_from_command,
        infer_step_from_event,
        normalize_event_payload,
        normalize_result_payload,
        normalize_scan_barcodes,
        require_str_field,
        resolve_first_str,
        resolve_path,
        resolve_step_from_context,
    )
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
        SmtClassifierDeviceRole,
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
    "BARCODE_LIST_KEY",
    "COMMAND_CODE_ALIASES",
    "COMMAND_FIELD_RULES",
    "CONTRACT_VERSION",
    "DEVICE_CODE_ALIASES",
    "EVENT_FIELD_ALIASES",
    "RESULT_FIELD_ALIASES",
    "STATES",
    "STEP_CODE_KEY",
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
    "SmtClassifierPlugin",
    "SmtClassifierResultType",
    "SmtClassifierStage",
    "SmtClassifierStageMachine",
    "SmtClassifierStageStatus",
    "SmtClassifierStateMachine",
    "SmtClassifierStatus",
    "SmtClassifierStepCode",
    "TaskType",
    "ensure_dict",
    "generate_move_forward_command",
    "generate_pick_and_put_command",
    "get_valid_stage_transitions",
    "get_valid_transitions",
    "infer_step_from_command",
    "infer_step_from_event",
    "normalize_event_payload",
    "normalize_result_payload",
    "normalize_scan_barcodes",
    "require_str_field",
    "resolve_first_str",
    "resolve_path",
    "resolve_step_from_context",
    "smt_classifier_event_handler",
    "smt_classifier_plugin",
]

_EXPORT_SOURCES: dict[str, tuple[str, str]] = {
    "BARCODE_LIST_KEY": ("src.workline_plugins.smt_classifier.contract", "BARCODE_LIST_KEY"),
    "COMMAND_FIELD_RULES": ("src.workline_plugins.smt_classifier.contract", "COMMAND_FIELD_RULES"),
    "COMMAND_CODE_ALIASES": ("src.workline_plugins.smt_classifier.contract", "COMMAND_CODE_ALIASES"),
    "CommandResult": ("src.workline_plugins.smt_classifier.event_handlers", "CommandResult"),
    "CommandResultData": ("src.workline_plugins.smt_classifier.event_handlers", "CommandResultData"),
    "CONTRACT_VERSION": ("src.workline_plugins.smt_classifier.contract", "CONTRACT_VERSION"),
    "DEVICE_CODE_ALIASES": ("src.workline_plugins.smt_classifier.contract", "DEVICE_CODE_ALIASES"),
    "DeviceRoleRequirement": ("src.workline_plugins.smt_classifier.plugin", "DeviceRoleRequirement"),
    "EVENT_FIELD_ALIASES": ("src.workline_plugins.smt_classifier.contract", "EVENT_FIELD_ALIASES"),
    "ErrorDetail": ("src.workline_plugins.smt_classifier.event_handlers", "ErrorDetail"),
    "EventType": ("src.workline_plugins.smt_classifier.event_handlers", "EventType"),
    "LocationInfo": ("src.workline_plugins.smt_classifier.event_handlers", "LocationInfo"),
    "LocationType": ("src.workline_plugins.smt_classifier.event_handlers", "LocationType"),
    "RESULT_FIELD_ALIASES": ("src.workline_plugins.smt_classifier.contract", "RESULT_FIELD_ALIASES"),
    "STATES": ("src.workline_plugins.smt_classifier.state_machine", "STATES"),
    "STEP_CODE_KEY": ("src.workline_plugins.smt_classifier.contract", "STEP_CODE_KEY"),
    "SmtClassifierCommandType": ("src.workline_plugins.smt_classifier.contract", "SmtClassifierCommandType"),
    "SmtClassifierDeviceRole": ("src.workline_plugins.smt_classifier.plugin", "SmtClassifierDeviceRole"),
    "SmtClassifierEventHandler": (
        "src.workline_plugins.smt_classifier.event_handlers",
        "SmtClassifierEventHandler",
    ),
    "SmtClassifierEventType": ("src.workline_plugins.smt_classifier.contract", "SmtClassifierEventType"),
    "SmtClassifierPlugin": ("src.workline_plugins.smt_classifier.plugin", "SmtClassifierPlugin"),
    "SmtClassifierResultType": ("src.workline_plugins.smt_classifier.contract", "SmtClassifierResultType"),
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
    "SmtClassifierStepCode": ("src.workline_plugins.smt_classifier.contract", "SmtClassifierStepCode"),
    "TRANSITIONS": ("src.workline_plugins.smt_classifier.state_machine", "TRANSITIONS"),
    "TaskType": ("src.workline_plugins.smt_classifier.event_handlers", "TaskType"),
    "ensure_dict": ("src.workline_plugins.smt_classifier.contract", "ensure_dict"),
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
    "infer_step_from_command": ("src.workline_plugins.smt_classifier.contract", "infer_step_from_command"),
    "infer_step_from_event": ("src.workline_plugins.smt_classifier.contract", "infer_step_from_event"),
    "normalize_event_payload": ("src.workline_plugins.smt_classifier.contract", "normalize_event_payload"),
    "normalize_result_payload": ("src.workline_plugins.smt_classifier.contract", "normalize_result_payload"),
    "normalize_scan_barcodes": ("src.workline_plugins.smt_classifier.contract", "normalize_scan_barcodes"),
    "require_str_field": ("src.workline_plugins.smt_classifier.contract", "require_str_field"),
    "resolve_first_str": ("src.workline_plugins.smt_classifier.contract", "resolve_first_str"),
    "resolve_path": ("src.workline_plugins.smt_classifier.contract", "resolve_path"),
    "resolve_step_from_context": ("src.workline_plugins.smt_classifier.contract", "resolve_step_from_context"),
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
