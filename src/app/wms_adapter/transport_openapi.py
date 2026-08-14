"""WES 接收 WMS Transport callback 的共享机器合同。"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

from src.app.wms_adapter.transport_wire import (
    EVENT_PATH,
    POSITION_OPERATION,
    RESULT_OPERATION,
    SIGNED_INT64_MAX,
    TRANSPORT_FAILURE_CODES,
)

_NONBLANK_PATTERN = r".*\S.*"


def _closed_object(required: list[str], properties: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


def _literal(value: object) -> dict[str, object]:
    return {"type": "boolean" if isinstance(value, bool) else "string", "enum": [value]}


_UUIDV7_SCHEMA = {
    "type": "string",
    "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    "description": "WMS 生成的小写 canonical UUIDv7 幂等号",
}
_TIMESTAMP_SCHEMA = {
    "type": "integer",
    "format": "int64",
    "minimum": 0,
    "maximum": SIGNED_INT64_MAX,
    "description": "Unix 毫秒时间戳",
}
_TRANSPORT_TASK_ID_SCHEMA = {"type": "string", "minLength": 1, "maxLength": 80, "pattern": _NONBLANK_PATTERN}
_OBJECT_ID_SCHEMA = {"type": "string", "minLength": 1, "maxLength": 100, "pattern": _NONBLANK_PATTERN}
_POSITION_TEXT_SCHEMA = {"type": "string", "minLength": 1, "maxLength": 100, "pattern": _NONBLANK_PATTERN}
_RACK_POSITION_SCHEMA = _closed_object(
    ["kind", "location_code"],
    {"kind": _literal("RACK_POSITION"), "location_code": _POSITION_TEXT_SCHEMA},
)
_RACK_BIN_SLOT_SCHEMA = _closed_object(
    ["kind", "rack_id", "rack_face", "slot_id"],
    {
        "kind": _literal("RACK_BIN_SLOT"),
        "rack_id": _POSITION_TEXT_SCHEMA,
        "rack_face": {"type": "string", "enum": ["A", "B"]},
        "slot_id": _POSITION_TEXT_SCHEMA,
    },
)
_HANDOFF_POSITION_SCHEMA = _closed_object(
    ["kind", "location_code"],
    {"kind": _literal("HANDOFF_POSITION"), "location_code": _POSITION_TEXT_SCHEMA},
)
_BIN_POSITION_SCHEMA = {"oneOf": [_RACK_BIN_SLOT_SCHEMA, _HANDOFF_POSITION_SCHEMA]}

_POSITION_DATA_SCHEMA = {
    "oneOf": [
        _closed_object(
            ["transport_task_id", "bin_id", "milestone"],
            {
                "transport_task_id": _TRANSPORT_TASK_ID_SCHEMA,
                "bin_id": _OBJECT_ID_SCHEMA,
                "milestone": _literal(milestone),
            },
        )
        for milestone in ("SOURCE_PICKED", "POSITION_UNKNOWN")
    ]
    + [
        _closed_object(
            ["transport_task_id", "bin_id", "milestone", "final_position"],
            {
                "transport_task_id": _TRANSPORT_TASK_ID_SCHEMA,
                "bin_id": _OBJECT_ID_SCHEMA,
                "milestone": _literal("TARGET_PLACED"),
                "final_position": _BIN_POSITION_SCHEMA,
            },
        )
    ]
}


def _member_result_schema(*, final_position: Mapping[str, object], arrival_face: bool) -> dict[str, object]:
    success_properties: dict[str, object] = {
        "object_id": _OBJECT_ID_SCHEMA,
        "status": _literal("SUCCEEDED"),
        "final_position": final_position,
    }
    failed_properties: dict[str, object] = {
        "object_id": _OBJECT_ID_SCHEMA,
        "status": _literal("FAILED"),
        "final_position": final_position,
        "failure_code": {
            "type": "string",
            "enum": sorted(TRANSPORT_FAILURE_CODES - {"POSITION_UNKNOWN"}),
        },
    }
    success_required = ["object_id", "status", "final_position"]
    failed_required = ["object_id", "status", "final_position", "failure_code"]
    if arrival_face:
        arrival_schema = {"type": "string", "enum": ["A", "B"]}
        success_properties["arrival_face"] = arrival_schema
        failed_properties["arrival_face"] = arrival_schema
        success_required.append("arrival_face")
        failed_required.append("arrival_face")
    return {
        "oneOf": [
            _closed_object(success_required, success_properties),
            _closed_object(failed_required, failed_properties),
            _closed_object(
                ["object_id", "status", "position_unknown", "failure_code"],
                {
                    "object_id": _OBJECT_ID_SCHEMA,
                    "status": _literal("FAILED"),
                    "position_unknown": _literal(True),
                    "failure_code": _literal("POSITION_UNKNOWN"),
                },
            ),
        ]
    }


def _result_data_schema(kind_values: list[str], result_schema: dict[str, object]) -> dict[str, object]:
    return _closed_object(
        ["transport_task_id", "kind", "outcome_revision", "results"],
        {
            "transport_task_id": _TRANSPORT_TASK_ID_SCHEMA,
            "kind": {"type": "string", "enum": kind_values},
            "outcome_revision": {
                "type": "integer",
                "format": "int64",
                "minimum": 1,
                "maximum": SIGNED_INT64_MAX,
            },
            "results": {"type": "array", "minItems": 1, "items": result_schema},
        },
    )


_RESULT_DATA_SCHEMA = {
    "oneOf": [
        _result_data_schema(
            ["RACK_MOVE", "RACK_ROTATE"],
            _member_result_schema(final_position=_RACK_POSITION_SCHEMA, arrival_face=True),
        ),
        _result_data_schema(
            ["BIN_MOVE", "BIN_EXCHANGE"],
            _member_result_schema(final_position=_BIN_POSITION_SCHEMA, arrival_face=False),
        ),
    ]
}


def _event_envelope_schema(operation: str, data_schema: Mapping[str, object]) -> dict[str, object]:
    return _closed_object(
        ["operation_id", "operation", "timestamp", "data"],
        {
            "operation_id": _UUIDV7_SCHEMA,
            "operation": _literal(operation),
            "timestamp": _TIMESTAMP_SCHEMA,
            "data": data_schema,
        },
    )


TRANSPORT_EVENT_REQUEST_SCHEMA = {
    "oneOf": [
        _event_envelope_schema(POSITION_OPERATION, _POSITION_DATA_SCHEMA),
        _event_envelope_schema(RESULT_OPERATION, _RESULT_DATA_SCHEMA),
    ]
}


def _ack_schema(code: str, data_schema: dict[str, object]) -> dict[str, object]:
    return _closed_object(
        ["operation_id", "code", "timestamp", "data"],
        {
            "operation_id": _UUIDV7_SCHEMA,
            "code": _literal(code),
            "timestamp": _TIMESTAMP_SCHEMA,
            "data": data_schema,
        },
    )


_ACK_TASK_DATA_SCHEMA = _closed_object(["transport_task_id"], {"transport_task_id": _TRANSPORT_TASK_ID_SCHEMA})
_REASON_DATA_SCHEMA = _closed_object(
    ["reason_code"],
    {"reason_code": {"type": "string", "enum": ["INVALID_EVIDENCE", "UNSUPPORTED_OPERATION"]}},
)

TRANSPORT_EVENT_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "重复 evidence 已确认",
        "content": {"application/json": {"schema": _ack_schema("DUPLICATE", _ACK_TASK_DATA_SCHEMA)}},
    },
    202: {
        "description": "evidence 已持久化",
        "content": {"application/json": {"schema": _ack_schema("RECEIVED", _ACK_TASK_DATA_SCHEMA)}},
    },
    400: {"description": "请求媒体类型、编码或 evidence envelope 不满足封闭合同"},
    401: {"description": "部署 profile 不允许无签名 callback", "x-operational-error": True},
    409: {
        "description": "operation_id 或 outcome_revision 身份冲突",
        "content": {"application/json": {"schema": _ack_schema("CONFLICT", _ACK_TASK_DATA_SCHEMA)}},
    },
    413: {"description": "请求体超过固定上限"},
    422: {
        "description": "evidence data 不满足对应 operation 的封闭合同",
        "content": {"application/json": {"schema": _ack_schema("REJECTED", _REASON_DATA_SCHEMA)}},
    },
    503: {
        "description": "Transport runtime 尚未就绪或当前无法可靠持久化",
        "content": {"application/json": {"schema": _ack_schema("UNAVAILABLE", _closed_object([], {}))}},
    },
}


def build_transport_openapi_document() -> dict[str, object]:
    """生成仅包含 WMS callback 的可外发 OpenAPI 3.0.3 合同。"""

    return {
        "openapi": "3.0.3",
        "info": {"title": "WES-WMS Transport Callback API", "version": "1.0.0"},
        "paths": {
            EVENT_PATH: {
                "post": {
                    "operationId": "receiveWmsTransportEvent",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": deepcopy(TRANSPORT_EVENT_REQUEST_SCHEMA)}},
                    },
                    "responses": {
                        str(code): deepcopy(response) for code, response in TRANSPORT_EVENT_RESPONSES.items()
                    },
                }
            }
        },
    }


__all__ = [
    "TRANSPORT_EVENT_REQUEST_SCHEMA",
    "TRANSPORT_EVENT_RESPONSES",
    "build_transport_openapi_document",
]
