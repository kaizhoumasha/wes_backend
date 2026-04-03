"""SMT classifier plugin contract.

This module is the single contract source for the SMT classifier plugin:
- command enums
- event enums
- result enums
- field alias mapping rules
- payload normalization and validation rules
- step resolution helpers for runtime progression
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Final, TypedDict

JsonDict = dict[str, Any]
CONTRACT_VERSION: Final[str] = "1.0"
DEVICE_CODE_ALIASES: Final[tuple[str, ...]] = ("device_code", "device_id")
COMMAND_CODE_ALIASES: Final[tuple[str, ...]] = ("command_code", "command_id")
BARCODE_LIST_KEY: Final[str] = "barcode_list"


class SmtClassifierEventType(str, Enum):
    """Vendor event enum accepted by the plugin."""

    SCAN_COMPLETED = "SCAN_COMPLETED"
    ESTOP_PRESSED = "ESTOP_PRESSED"
    INSPECTION_COMPLETED = "INSPECTION_COMPLETED"


class SmtClassifierCommandType(str, Enum):
    """Vendor command enum produced by the plugin."""

    PICK_AND_PUT = "PICK_AND_PUT"
    MOVE_FORWARD = "MOVE_FORWARD"


class SmtClassifierResultType(str, Enum):
    """Vendor result enum accepted by the plugin."""

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class SmtClassifierStepCode(str, Enum):
    """Step-based runtime progression contract for command/result flow."""

    WAITING_SCAN_EVENT = "WAITING_SCAN_EVENT"
    INPUT_PICK_PLACE = "INPUT_PICK_PLACE"
    WAITING_INSPECTION_EVENT = "WAITING_INSPECTION_EVENT"
    NG_PICK_PLACE = "NG_PICK_PLACE"
    PIPELINE_MOVE_FORWARD = "PIPELINE_MOVE_FORWARD"
    WAITING_AGV_DELIVERY = "WAITING_AGV_DELIVERY"
    OUTPUT_PICK_PLACE = "OUTPUT_PICK_PLACE"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


class ContractFieldAliases(TypedDict):
    """Alias paths for a canonical contract field."""

    canonical: str
    aliases: tuple[str, ...]


EVENT_FIELD_ALIASES: Final[dict[str, ContractFieldAliases]] = {
    "event_type": {
        "canonical": "event_type",
        "aliases": ("event_type",),
    },
    "location_id": {
        "canonical": "location_id",
        "aliases": ("location_id", "data.location_id", "data.location"),
    },
    "barcode": {
        "canonical": "barcode",
        "aliases": ("data.barcode", "barcode"),
    },
    "scan_result": {
        "canonical": "scan_result",
        "aliases": ("data.result", "data.scan_result", "scan_result"),
    },
    "inspection_result": {
        "canonical": "inspection_result",
        "aliases": ("data.result", "data.inspection_result", "inspection_result"),
    },
}

RESULT_FIELD_ALIASES: Final[dict[str, ContractFieldAliases]] = {
    "command_type": {
        "canonical": "command_type",
        "aliases": ("command_type", "data.command_type", "task_type", "data.task_type"),
    },
    "result": {
        "canonical": "result",
        "aliases": ("result", "data.result"),
    },
    "error_code": {
        "canonical": "error_code",
        "aliases": ("error_detail.code", "error_detail.error_code"),
    },
    "error_message": {
        "canonical": "error_message",
        "aliases": ("error_detail.message", "error_detail.error_message", "error_detail.msg"),
    },
}

COMMAND_FIELD_RULES: Final[dict[SmtClassifierCommandType, tuple[str, ...]]] = {
    SmtClassifierCommandType.PICK_AND_PUT: ("source_type", "target_type"),
    SmtClassifierCommandType.MOVE_FORWARD: ("source_type", "target_type"),
}

STEP_CODE_KEY: Final[str] = "step_code"


def resolve_path(payload: JsonDict, path: str) -> Any:
    """Resolve a dotted key path from payload."""

    current: Any = payload
    for segment in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(segment)
    return current


def resolve_first_str(payload: JsonDict, aliases: tuple[str, ...], default: str = "") -> str:
    """Resolve the first non-empty string value from alias paths."""

    for alias in aliases:
        value = resolve_path(payload, alias)
        if isinstance(value, str) and value:
            return value
    return default


def resolve_step_from_context(context: JsonDict) -> SmtClassifierStepCode | None:
    """Resolve the step from context contract field."""

    step_raw = context.get(STEP_CODE_KEY)
    if not isinstance(step_raw, str) or not step_raw:
        return None
    try:
        return SmtClassifierStepCode(step_raw)
    except ValueError:
        return None


def ensure_dict(value: Any, field_name: str) -> JsonDict:
    """Ensure the incoming value is a JSON object."""

    if isinstance(value, dict):
        return value
    raise ValueError(f"{field_name} must be an object")


def require_str_field(payload: JsonDict, aliases: tuple[str, ...], field_name: str) -> str:
    """Resolve a required string field from aliases."""

    value = resolve_first_str(payload, aliases)
    if value:
        return value
    raise ValueError(f"{field_name} is required")


def normalize_scan_barcodes(data: JsonDict) -> list[str]:
    """Collect barcode payload from canonical and vendor fields."""

    barcodes: list[str] = []

    direct_barcode = resolve_first_str(data, ("barcode",))
    if direct_barcode:
        barcodes.append(direct_barcode)

    for index in range(1, 7):
        barcode = data.get(f"barcode{index}")
        if isinstance(barcode, str) and barcode:
            barcodes.append(barcode)

    deduplicated: list[str] = []
    seen: set[str] = set()
    for barcode in barcodes:
        if barcode in seen:
            continue
        seen.add(barcode)
        deduplicated.append(barcode)
    return deduplicated


def infer_step_from_event(event_type: str) -> SmtClassifierStepCode | None:
    """Infer initial step semantics from an accepted event type."""

    if event_type == SmtClassifierEventType.SCAN_COMPLETED.value:
        return SmtClassifierStepCode.WAITING_SCAN_EVENT
    if event_type == SmtClassifierEventType.INSPECTION_COMPLETED.value:
        return SmtClassifierStepCode.WAITING_INSPECTION_EVENT
    if event_type == SmtClassifierEventType.ESTOP_PRESSED.value:
        return SmtClassifierStepCode.ERROR
    return None


def normalize_event_payload(payload: JsonDict) -> JsonDict:
    """Validate and normalize vendor event payload into WES canonical fields."""

    device_code = require_str_field(payload, DEVICE_CODE_ALIASES, "device_code")
    event_type = require_str_field(payload, EVENT_FIELD_ALIASES["event_type"]["aliases"], "event_type")
    try:
        accepted_event_type = SmtClassifierEventType(event_type).value
    except ValueError as exc:
        raise ValueError(f"unsupported event_type: {event_type}") from exc

    timestamp = payload.get("timestamp")
    if timestamp is not None and not isinstance(timestamp, int):
        raise ValueError("timestamp must be an integer")

    raw_data = payload.get("data")
    data = ensure_dict(raw_data, "data") if raw_data is not None else {}
    normalized_data: JsonDict = dict(data)

    if accepted_event_type == SmtClassifierEventType.SCAN_COMPLETED.value:
        location_id = require_str_field(data, ("location_id", "location"), "data.location")
        barcode_list = normalize_scan_barcodes(data)
        if not barcode_list:
            raise ValueError("SCAN_COMPLETED requires barcode or barcode1~barcode6")
        normalized_data["location"] = location_id
        normalized_data["location_id"] = location_id
        normalized_data["barcode"] = barcode_list[0]
        normalized_data[BARCODE_LIST_KEY] = barcode_list

    if accepted_event_type == SmtClassifierEventType.INSPECTION_COMPLETED.value:
        inspection_result = require_str_field(data, ("inspection_result", "result"), "data.inspection_result")
        normalized_data["inspection_result"] = inspection_result

    normalized_payload: JsonDict = {
        "device_code": device_code,
        "event_type": accepted_event_type,
        "data": normalized_data,
    }
    if isinstance(timestamp, int):
        normalized_payload["timestamp"] = timestamp
    return normalized_payload


def normalize_result_payload(payload: JsonDict) -> JsonDict:
    """Validate and normalize vendor command result payload into WES canonical fields."""

    command_code = require_str_field(payload, COMMAND_CODE_ALIASES, "command_code")
    device_code = require_str_field(payload, DEVICE_CODE_ALIASES, "device_code")
    result = require_str_field(payload, RESULT_FIELD_ALIASES["result"]["aliases"], "result")
    try:
        accepted_result = SmtClassifierResultType(result).value
    except ValueError as exc:
        raise ValueError(f"unsupported result: {result}") from exc

    finish_time = payload.get("finish_time")
    if not isinstance(finish_time, int):
        raise TypeError("finish_time must be an integer")

    raw_data = payload.get("data")
    data = ensure_dict(raw_data, "data") if raw_data is not None else {}
    normalized_data: JsonDict = dict(data)
    command_type = resolve_first_str(payload, RESULT_FIELD_ALIASES["command_type"]["aliases"])
    if command_type:
        try:
            normalized_data["command_type"] = SmtClassifierCommandType(command_type).value
        except ValueError as exc:
            raise ValueError(f"unsupported command_type: {command_type}") from exc

    normalized_payload: JsonDict = {
        "command_code": command_code,
        "device_code": device_code,
        "result": accepted_result,
        "finish_time": finish_time,
        "data": normalized_data,
    }

    if accepted_result == SmtClassifierResultType.FAILED.value or payload.get("error_detail") is not None:
        raw_error_detail = payload.get("error_detail")
        error_detail = ensure_dict(raw_error_detail, "error_detail") if raw_error_detail is not None else {}
        error_code = resolve_first_str(error_detail, ("code", "error_code"))
        error_message = resolve_first_str(error_detail, ("message", "error_message", "msg"))
        if accepted_result == SmtClassifierResultType.FAILED.value and not error_code:
            raise ValueError("FAILED result requires error_detail.code")
        normalized_payload["error_detail"] = {
            "code": error_code,
            "message": error_message,
        }

    return normalized_payload


def infer_step_from_command(command_type: str, context: JsonDict) -> SmtClassifierStepCode | None:
    """Backward-compatible fallback when older records have no step_code."""

    if command_type == SmtClassifierCommandType.MOVE_FORWARD.value:
        return SmtClassifierStepCode.PIPELINE_MOVE_FORWARD
    if command_type != SmtClassifierCommandType.PICK_AND_PUT.value:
        return None

    pick_place_reason = context.get("pick_place_reason")
    if isinstance(pick_place_reason, str) and pick_place_reason == "OUTPUT":
        return SmtClassifierStepCode.OUTPUT_PICK_PLACE

    ng_reason = context.get("ng_reason")
    if isinstance(ng_reason, str) and ng_reason in ("SCAN_NG", "INSPECTION_NG"):
        return SmtClassifierStepCode.NG_PICK_PLACE

    target_type = context.get("target_type")
    if isinstance(target_type, str):
        if target_type == "NG_PLATFORM":
            return SmtClassifierStepCode.NG_PICK_PLACE
        if target_type in ("OUTPUT_PLATFORM", "BIN"):
            return SmtClassifierStepCode.OUTPUT_PICK_PLACE
        if target_type == "PIPELINE_PLATFORM":
            return SmtClassifierStepCode.INPUT_PICK_PLACE

    return SmtClassifierStepCode.INPUT_PICK_PLACE


__all__ = [
    "BARCODE_LIST_KEY",
    "COMMAND_CODE_ALIASES",
    "COMMAND_FIELD_RULES",
    "CONTRACT_VERSION",
    "DEVICE_CODE_ALIASES",
    "EVENT_FIELD_ALIASES",
    "RESULT_FIELD_ALIASES",
    "STEP_CODE_KEY",
    "JsonDict",
    "SmtClassifierCommandType",
    "SmtClassifierEventType",
    "SmtClassifierResultType",
    "SmtClassifierStepCode",
    "ensure_dict",
    "infer_step_from_command",
    "infer_step_from_event",
    "normalize_event_payload",
    "normalize_result_payload",
    "normalize_scan_barcodes",
    "require_str_field",
    "resolve_first_str",
    "resolve_path",
    "resolve_step_from_context",
]
