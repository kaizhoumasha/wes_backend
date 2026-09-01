"""Transport 与 WMS 之间的固定线上接口（wire）转换。"""

from __future__ import annotations

from typing import Any

from src.app.transport.contracts import (
    TRANSPORT_POSITION_OPERATION,
    TRANSPORT_RESULT_OPERATION,
    TransportContractError,
)
from src.core.uuid7 import is_uuid7

POSITION_OPERATION = TRANSPORT_POSITION_OPERATION
RESULT_OPERATION = TRANSPORT_RESULT_OPERATION
EVENT_PATH = "/api/v1/wms/events"
SIGNED_INT64_MIN = 0
SIGNED_INT64_MAX = 2**63 - 1
TRANSPORT_FAILURE_CODES = frozenset({"RCS_TASK_REJECTED", "RCS_EXECUTION_FAILED", "POSITION_UNKNOWN", "MANUAL_ABORTED"})


class UnsupportedTransportOperation(TransportContractError):
    """callback operation 不属于当前 Transport 闭集。"""


def validate_callback_envelope(value: object) -> dict[str, Any]:
    envelope = _strict_dict(value, {"operation_id", "operation", "timestamp", "data"}, "callback envelope")
    operation_id = _nonblank(envelope["operation_id"], "operation_id")
    if not is_uuid7(operation_id) or operation_id != operation_id.lower():
        raise TransportContractError("operation_id must be a lowercase canonical UUIDv7")
    if not isinstance(envelope["timestamp"], int) or isinstance(envelope["timestamp"], bool):
        raise TransportContractError("timestamp must be an integer")
    if not 0 <= envelope["timestamp"] <= SIGNED_INT64_MAX:
        raise TransportContractError("timestamp must be a non-negative signed 64-bit integer")
    operation = envelope["operation"]
    if operation == POSITION_OPERATION:
        envelope["data"] = _validate_position_data(envelope["data"])
    elif operation == RESULT_OPERATION:
        envelope["data"] = _validate_result_data(envelope["data"])
    else:
        raise UnsupportedTransportOperation("unsupported transport callback operation")
    return envelope


def _validate_position_data(value: object) -> dict[str, Any]:
    data = _strict_dict(
        value,
        {"transport_task_id", "container_id", "milestone"},
        "position data",
        optional={"final_position"},
    )
    _nonblank(data["transport_task_id"], "transport_task_id", max_length=80)
    _nonblank(data["container_id"], "container_id", max_length=100)
    milestone = data["milestone"]
    if milestone not in {"SOURCE_PICKED", "TARGET_PLACED", "POSITION_UNKNOWN"}:
        raise TransportContractError("invalid position milestone")
    if milestone == "TARGET_PLACED":
        if "final_position" not in data:
            raise TransportContractError("TARGET_PLACED requires final_position")
        final_position = _validate_position(data["final_position"])
        if final_position["kind"] not in {"RACK_BIN_SLOT", "HANDOFF_POSITION"}:
            raise TransportContractError("TARGET_PLACED position must be a rack bin slot or handoff position")
    elif "final_position" in data:
        raise TransportContractError("final_position is only valid for TARGET_PLACED")
    return data


def _validate_result_data(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise TransportContractError("result data must be an object")
    kind = value.get("kind")
    if kind not in {"RACK_MOVE", "RACK_ROTATE", "BIN_MOVE", "BIN_EXCHANGE"}:
        raise TransportContractError("invalid transport kind")
    if kind in {"RACK_MOVE", "RACK_ROTATE"}:
        data = _strict_dict(
            value,
            {"transport_task_id", "kind", "outcome_revision", "rack_id", "status"},
            "result data",
            optional={"final_position", "position_unknown", "failure_code", "arrival_face"},
        )
    else:
        data = _strict_dict(value, {"transport_task_id", "kind", "outcome_revision", "results"}, "result data")
    _nonblank(data["transport_task_id"], "transport_task_id", max_length=80)
    outcome_revision = data["outcome_revision"]
    if (
        not isinstance(outcome_revision, int)
        or isinstance(outcome_revision, bool)
        or not 1 <= outcome_revision <= SIGNED_INT64_MAX
    ):
        raise TransportContractError("outcome_revision must be a positive integer within signed 64-bit range")
    if kind in {"RACK_MOVE", "RACK_ROTATE"}:
        _validate_member_result(
            {
                key: data[key]
                for key in ("rack_id", "status", "final_position", "position_unknown", "failure_code", "arrival_face")
                if key in data
            },
            id_field="rack_id",
            rack_kind=True,
        )
        return data
    results = data["results"]
    if not isinstance(results, list) or not results:
        raise TransportContractError("results must be a non-empty list")
    if kind == "BIN_MOVE" and len(results) > 4:
        raise TransportContractError("BIN_MOVE results must contain 1 to 4 members")
    if kind == "BIN_EXCHANGE" and len(results) not in {2, 4}:
        raise TransportContractError("BIN_EXCHANGE results must contain 2 or 4 members")
    normalized: list[dict[str, Any]] = []
    container_ids: set[str] = set()
    for raw in results:
        result = _validate_member_result(raw, id_field="container_id", rack_kind=False)
        container_id = result["container_id"]
        if container_id in container_ids:
            raise TransportContractError("duplicate result container_id")
        container_ids.add(container_id)
        normalized.append(result)
    if [result["container_id"] for result in normalized] != sorted(container_ids):
        raise TransportContractError("results must be sorted by container_id")
    data["results"] = normalized
    return data


def _validate_member_result(value: object, *, id_field: str, rack_kind: bool) -> dict[str, Any]:
    result = _strict_dict(
        value,
        {id_field, "status"},
        "member result",
        optional={"final_position", "position_unknown", "failure_code", "arrival_face"},
    )
    _nonblank(result[id_field], id_field, max_length=100)
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
        final_position = _validate_position(result["final_position"])
        if rack_kind and final_position["kind"] != "RACK_POSITION":
            raise TransportContractError("rack result position must be RACK_POSITION")
        if not rack_kind and final_position["kind"] not in {"RACK_BIN_SLOT", "HANDOFF_POSITION"}:
            raise TransportContractError("bin result position must be a rack bin slot or handoff position")
    if status == "SUCCEEDED" and (not has_position or "failure_code" in result):
        raise TransportContractError("SUCCEEDED requires known position and no failure_code")
    if status == "FAILED":
        failure_code = _nonblank(result.get("failure_code"), "failure_code")
        if failure_code not in TRANSPORT_FAILURE_CODES:
            raise TransportContractError("invalid failure_code")
        if is_unknown != (failure_code == "POSITION_UNKNOWN"):
            raise TransportContractError("POSITION_UNKNOWN failure_code must match position_unknown=true")
    if rack_kind and has_position:
        arrival_face = result.get("arrival_face")
        if type(arrival_face) is not str or arrival_face == "":
            raise TransportContractError("known rack result requires arrival_face")
    if (not rack_kind or is_unknown) and "arrival_face" in result:
        raise TransportContractError("arrival_face is not valid for this result")
    return result


def _validate_position(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or "kind" not in value:
        raise TransportContractError("position must be an object with kind")
    kind = value["kind"]
    if kind == "RACK_POSITION":
        position = _strict_dict(value, {"kind", "location_code"}, "rack position")
        _nonblank(position["location_code"], "location_code", max_length=100)
        return position
    if kind == "RACK_BIN_SLOT":
        position = _strict_dict(value, {"kind", "rack_id", "rack_face", "slot_id"}, "rack bin slot")
        _nonblank(position["rack_id"], "rack_id", max_length=100)
        if type(position["rack_face"]) is not str or position["rack_face"] == "":
            raise TransportContractError("rack_face must be a non-empty string")
        _nonblank(position["slot_id"], "slot_id", max_length=100)
        return position
    if kind == "HANDOFF_POSITION":
        position = _strict_dict(value, {"kind", "location_code"}, "handoff position")
        _nonblank(position["location_code"], "location_code", max_length=100)
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
    keys = set(value)
    if not required <= keys or not keys <= allowed:
        raise TransportContractError(f"{field_name} fields do not match the closed contract")
    return dict(value)


def _nonblank(value: object, field_name: str, *, max_length: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TransportContractError(f"{field_name} must not be blank")
    if "\x00" in value:
        raise TransportContractError(f"{field_name} must not contain NUL")
    if max_length is not None and len(value) > max_length:
        raise TransportContractError(f"{field_name} exceeds {max_length} characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise TransportContractError(f"{field_name} must be valid UTF-8") from error
    return value


__all__ = [
    "EVENT_PATH",
    "POSITION_OPERATION",
    "RESULT_OPERATION",
    "TRANSPORT_FAILURE_CODES",
    "UnsupportedTransportOperation",
    "validate_callback_envelope",
]
