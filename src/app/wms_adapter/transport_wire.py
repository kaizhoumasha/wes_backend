"""Transport 与 WMS 之间的固定线上接口（wire）转换。"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from src.app.transport.contracts import TransportContractError, TransportRequest

POSITION_OPERATION = "transport.task.member_position_changed@v1"
RESULT_OPERATION = "transport.task.resulted@v1"
SUBMIT_OPERATION = "transport.task.submit@v1"
TRANSPORT_PATH = "/api/v1/wes/transport-requests"
EVENT_PATH = "/api/v1/wms/events"


def build_submit_data(request: TransportRequest, transport_task_id: str) -> dict[str, Any]:
    payload = _json_value(request)
    payload.pop("client_request_id")
    kind = payload.pop("kind")
    caller = payload.pop("caller")
    return {
        "transport_task_id": transport_task_id,
        "kind": kind,
        "caller": caller,
        **payload,
    }


def validate_callback_envelope(value: object) -> dict[str, Any]:
    envelope = _strict_dict(value, {"request_id", "operation", "timestamp", "data"}, "callback envelope")
    _nonblank(envelope["request_id"], "request_id")
    if not isinstance(envelope["timestamp"], int) or isinstance(envelope["timestamp"], bool):
        raise TransportContractError("timestamp must be an integer")
    operation = envelope["operation"]
    if operation == POSITION_OPERATION:
        envelope["data"] = _validate_position_data(envelope["data"])
    elif operation == RESULT_OPERATION:
        envelope["data"] = _validate_result_data(envelope["data"])
    else:
        raise TransportContractError("unsupported transport callback operation")
    return envelope


def _validate_position_data(value: object) -> dict[str, Any]:
    data = _strict_dict(
        value,
        {"event_id", "transport_task_id", "bin_id", "milestone"},
        "position data",
        optional={"final_position"},
    )
    for field in ("event_id", "transport_task_id", "bin_id"):
        _nonblank(data[field], field)
    milestone = data["milestone"]
    if milestone not in {"SOURCE_PICKED", "TARGET_PLACED", "POSITION_UNKNOWN"}:
        raise TransportContractError("invalid position milestone")
    if milestone == "TARGET_PLACED":
        if "final_position" not in data:
            raise TransportContractError("TARGET_PLACED requires final_position")
        _validate_position(data["final_position"])
    elif "final_position" in data:
        raise TransportContractError("final_position is only valid for TARGET_PLACED")
    return data


def _validate_result_data(value: object) -> dict[str, Any]:
    data = _strict_dict(value, {"event_id", "transport_task_id", "kind", "results"}, "result data")
    for field in ("event_id", "transport_task_id"):
        _nonblank(data[field], field)
    if data["kind"] not in {"RACK_MOVE", "RACK_ROTATE", "BIN_MOVE", "BIN_EXCHANGE"}:
        raise TransportContractError("invalid transport kind")
    results = data["results"]
    if not isinstance(results, list) or not results:
        raise TransportContractError("results must be a non-empty list")
    normalized: list[dict[str, Any]] = []
    object_ids: set[str] = set()
    for raw in results:
        result = _strict_dict(
            raw,
            {"object_id", "status"},
            "member result",
            optional={"final_position", "position_unknown", "failure_code", "arrival_face"},
        )
        object_id = _nonblank(result["object_id"], "object_id")
        if object_id in object_ids:
            raise TransportContractError("duplicate result object_id")
        object_ids.add(object_id)
        status = result["status"]
        if status not in {"SUCCEEDED", "FAILED"}:
            raise TransportContractError("invalid member result status")
        has_position = "final_position" in result
        is_unknown = result.get("position_unknown") is True
        if has_position == is_unknown:
            raise TransportContractError("final_position xor position_unknown=true is required")
        if "position_unknown" in result and result["position_unknown"] is not True:
            raise TransportContractError("position_unknown must be literal true")
        if has_position:
            _validate_position(result["final_position"])
        if status == "SUCCEEDED" and (not has_position or "failure_code" in result):
            raise TransportContractError("SUCCEEDED requires known position and no failure_code")
        if status == "FAILED":
            _nonblank(result.get("failure_code"), "failure_code")
        rack_kind = data["kind"] in {"RACK_MOVE", "RACK_ROTATE"}
        if rack_kind and has_position and result.get("arrival_face") not in {"A", "B"}:
            raise TransportContractError("known rack result requires arrival_face")
        if (not rack_kind or is_unknown) and "arrival_face" in result:
            raise TransportContractError("arrival_face is not valid for this result")
        normalized.append(result)
    data["results"] = normalized
    return data


def _validate_position(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or "kind" not in value:
        raise TransportContractError("position must be an object with kind")
    kind = value["kind"]
    if kind == "RACK_POSITION":
        position = _strict_dict(value, {"kind", "location_code"}, "rack position")
        _nonblank(position["location_code"], "location_code")
        return position
    if kind == "RACK_BIN_SLOT":
        position = _strict_dict(value, {"kind", "rack_id", "slot_id"}, "rack bin slot")
        _nonblank(position["rack_id"], "rack_id")
        _nonblank(position["slot_id"], "slot_id")
        return position
    if kind == "HANDOFF_POSITION":
        position = _strict_dict(value, {"kind", "location_code"}, "handoff position")
        _nonblank(position["location_code"], "location_code")
        return position
    raise TransportContractError("invalid position kind")


def _strict_dict(
    value: object,
    required: set[str],
    field_name: str,
    *,
    optional: set[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise TransportContractError(f"{field_name} must be an object")
    allowed = required | (optional or set())
    if (
        set(value) != required | (set(value) & (optional or set()))
        or not required <= set(value)
        or not set(value) <= allowed
    ):
        raise TransportContractError(f"{field_name} fields do not match the closed contract")
    return dict(value)


def _nonblank(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TransportContractError(f"{field_name} must not be blank")
    return value


def _json_value(value: object) -> dict[str, Any]:
    return json.loads(json.dumps(asdict(value), ensure_ascii=False, separators=(",", ":")))


__all__ = [
    "EVENT_PATH",
    "POSITION_OPERATION",
    "RESULT_OPERATION",
    "SUBMIT_OPERATION",
    "TRANSPORT_PATH",
    "build_submit_data",
    "validate_callback_envelope",
]
