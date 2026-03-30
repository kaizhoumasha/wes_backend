"""Generic workline plugin contract adapters."""

from __future__ import annotations

from importlib import import_module
from typing import Any, cast

from src.workline_plugin_registry import get_workline_plugin_definition

JsonDict = dict[str, Any]
STEP_CODE_FIELD = "step_code"


def _ensure_dict(value: Any) -> JsonDict:
    return cast("JsonDict", value) if isinstance(value, dict) else {}


def _resolve_first_str(payload: JsonDict, aliases: tuple[str, ...]) -> str:
    for alias in aliases:
        value = payload.get(alias)
        if isinstance(value, str) and value:
            return value
    return ""


def _load_contract_module(plugin_key: str | None) -> Any | None:
    definition = get_workline_plugin_definition(plugin_key)
    contract_module = getattr(definition, "contract_module", None)
    if not isinstance(contract_module, str) or not contract_module:
        return None
    return import_module(contract_module)


def resolve_contract_version(plugin_key: str | None) -> str | None:
    contract_module = _load_contract_module(plugin_key)
    if contract_module is None:
        return None

    version = getattr(contract_module, "CONTRACT_VERSION", None)
    return version if isinstance(version, str) and version else None


def _normalize_default_result_payload(payload: JsonDict) -> JsonDict:
    command_code = _resolve_first_str(payload, ("command_code", "command_id"))
    if not command_code:
        raise ValueError("command_code is required")

    device_code = _resolve_first_str(payload, ("device_code", "device_id"))
    if not device_code:
        raise ValueError("device_code is required")

    result = _resolve_first_str(payload, ("result",))
    if not result:
        raise ValueError("result is required")

    finish_time = payload.get("finish_time")
    if not isinstance(finish_time, int):
        raise ValueError("finish_time must be an integer")

    data = _ensure_dict(payload.get("data"))
    error_detail = _ensure_dict(payload.get("error_detail"))
    normalized_payload: JsonDict = {
        "command_code": command_code,
        "device_code": device_code,
        "result": result,
        "finish_time": finish_time,
        "data": data,
    }
    if error_detail:
        normalized_payload["error_detail"] = {
            "code": _resolve_first_str(error_detail, ("code", "error_code")),
            "message": _resolve_first_str(error_detail, ("message", "error_message", "msg")),
        }
    return normalized_payload


def _normalize_default_event_payload(payload: JsonDict) -> JsonDict:
    device_code = _resolve_first_str(payload, ("device_code", "device_id"))
    if not device_code:
        raise ValueError("device_code is required")

    event_type = _resolve_first_str(payload, ("event_type",))
    if not event_type:
        raise ValueError("event_type is required")

    normalized_payload: JsonDict = {
        "device_code": device_code,
        "event_type": event_type,
        "data": _ensure_dict(payload.get("data")),
    }

    timestamp = payload.get("timestamp")
    if timestamp is not None:
        if not isinstance(timestamp, int):
            raise ValueError("timestamp must be an integer")
        normalized_payload["timestamp"] = timestamp

    return normalized_payload


def normalize_callback_event_payload(
    *,
    plugin_key: str | None,
    payload: JsonDict,
) -> tuple[JsonDict, str | None, str | None]:
    contract_module = _load_contract_module(plugin_key)
    if contract_module is None:
        return _normalize_default_event_payload(payload), None, None

    normalize_event_payload = getattr(contract_module, "normalize_event_payload", None)
    if not callable(normalize_event_payload):
        return _normalize_default_event_payload(payload), None, None

    normalized_payload = cast("JsonDict", normalize_event_payload(payload))
    inferred_step_code: str | None = None
    infer_step_from_event = getattr(contract_module, "infer_step_from_event", None)
    if callable(infer_step_from_event):
        step = infer_step_from_event(cast("str", normalized_payload["event_type"]))
        step_value = getattr(step, "value", step)
        if isinstance(step_value, str) and step_value:
            inferred_step_code = step_value

    return normalized_payload, resolve_contract_version(plugin_key), inferred_step_code


def normalize_callback_result_payload(
    *,
    plugin_key: str | None,
    payload: JsonDict,
) -> tuple[JsonDict, str | None]:
    contract_module = _load_contract_module(plugin_key)
    if contract_module is None:
        return _normalize_default_result_payload(payload), None

    normalize_result_payload = getattr(contract_module, "normalize_result_payload", None)
    if not callable(normalize_result_payload):
        return _normalize_default_result_payload(payload), None

    normalized_payload = cast("JsonDict", normalize_result_payload(payload))
    return normalized_payload, resolve_contract_version(plugin_key)


__all__ = [
    "STEP_CODE_FIELD",
    "JsonDict",
    "normalize_callback_event_payload",
    "normalize_callback_result_payload",
    "resolve_contract_version",
]
