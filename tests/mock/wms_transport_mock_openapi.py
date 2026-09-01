"""WMS Transport Mock 的 Swagger 元数据与示例。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.app.wms_adapter.transport_openapi import TRANSPORT_EVENT_REQUEST_SCHEMA

WMS_TRANSPORT_CONTRACT_TAG = "WMS Transport Contract"
MOCK_DEBUG_TAG = "Mock Debug"

OPENAPI_TAGS = [
    {
        "name": WMS_TRANSPORT_CONTRACT_TAG,
        "description": "WMS 与 WES 之间的 Transport 提交合同。",
    },
    {
        "name": MOCK_DEBUG_TAG,
        "description": "Mock 的重置、故障注入、callback 转发和状态探针。",
    },
]

_NONBLANK_TEXT_SCHEMA = {
    "type": "string",
    "minLength": 1,
    "maxLength": 100,
    "pattern": r".*\S.*",
    "description": "非空、不得包含 NUL 的 UTF-8 文本；运行时同时校验。",
}
_TRANSPORT_TASK_ID_SCHEMA = {**_NONBLANK_TEXT_SCHEMA, "maxLength": 80}
_REJECTION_REASON_SCHEMA = {
    "type": "string",
    "enum": [
        "INVALID_ENVELOPE",
        "UNSUPPORTED_OPERATION",
        "INVALID_DATA",
        "COORDINATED_BIN_EXCHANGE_UNSUPPORTED",
    ],
}
_UUIDV7_SCHEMA = {
    "type": "string",
    "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    "description": "小写 canonical UUIDv7 operation_id。",
}
_TIMESTAMP_SCHEMA = {"type": "integer", "format": "int64", "minimum": 0, "maximum": 2**63 - 1}
_FACE_SCHEMA = {
    "type": "string",
    "minLength": 1,
    "description": "Opaque non-empty face value; preserve exactly",
}
_RCS_TEMPLATE_SCHEMA = {"type": "string", "enum": ["CTU01", "CTU02", "CTU03", "F01"]}


def _closed_object(
    required: list[str], properties: dict[str, object], *, description: str | None = None
) -> dict[str, object]:
    schema: dict[str, object] = {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }
    if description is not None:
        schema["description"] = description
    return schema


_RACK_POSITION_SCHEMA = _closed_object(
    ["kind", "location_code"],
    {"kind": {"type": "string", "enum": ["RACK_POSITION"]}, "location_code": _NONBLANK_TEXT_SCHEMA},
)
_RACK_REFERENCE_SCHEMA = _closed_object(
    ["kind", "location_code"],
    {"kind": {"type": "string", "enum": ["RACK"]}, "location_code": _NONBLANK_TEXT_SCHEMA},
)
_ZONE_POSITION_SCHEMA = _closed_object(
    ["kind", "location_code"],
    {"kind": {"type": "string", "enum": ["ZONE"]}, "location_code": _NONBLANK_TEXT_SCHEMA},
)
_RACK_MOVE_POSITION_SCHEMA = {"oneOf": [_RACK_REFERENCE_SCHEMA, _ZONE_POSITION_SCHEMA, _RACK_POSITION_SCHEMA]}
_RACK_BIN_SLOT_SCHEMA = _closed_object(
    ["kind", "rack_id", "rack_face", "slot_id"],
    {
        "kind": {"type": "string", "enum": ["RACK_BIN_SLOT"]},
        "rack_id": _NONBLANK_TEXT_SCHEMA,
        "rack_face": _FACE_SCHEMA,
        "slot_id": _NONBLANK_TEXT_SCHEMA,
    },
)
_HANDOFF_POSITION_SCHEMA = _closed_object(
    ["kind", "location_code"],
    {"kind": {"type": "string", "enum": ["HANDOFF_POSITION"]}, "location_code": _NONBLANK_TEXT_SCHEMA},
)
_BIN_MOVE_SCHEMA = _closed_object(
    ["container_id", "source", "target"],
    {
        "container_id": _NONBLANK_TEXT_SCHEMA,
        "source": {"oneOf": [_RACK_BIN_SLOT_SCHEMA, _HANDOFF_POSITION_SCHEMA]},
        "target": {"oneOf": [_RACK_BIN_SLOT_SCHEMA, _HANDOFF_POSITION_SCHEMA]},
    },
)
_BIN_EXCHANGE_MOVE_SCHEMA = _closed_object(
    ["container_id", "source", "target"],
    {"container_id": _NONBLANK_TEXT_SCHEMA, "source": _RACK_BIN_SLOT_SCHEMA, "target": _RACK_BIN_SLOT_SCHEMA},
)

_RACK_MOVE_DATA_SCHEMA = _closed_object(
    ["transport_task_id", "kind", "rack_id", "source", "target", "target_face", "rcs_template_id"],
    {
        "transport_task_id": _TRANSPORT_TASK_ID_SCHEMA,
        "kind": {"type": "string", "enum": ["RACK_MOVE"]},
        "rack_id": _NONBLANK_TEXT_SCHEMA,
        "source": _RACK_MOVE_POSITION_SCHEMA,
        "target": _RACK_MOVE_POSITION_SCHEMA,
        "target_face": _FACE_SCHEMA,
        "rcs_template_id": _RCS_TEMPLATE_SCHEMA,
    },
    description="运行时校验 source 与 target 不同。",
)
_RACK_ROTATE_DATA_SCHEMA = _closed_object(
    ["transport_task_id", "kind", "rack_id", "source", "target", "target_face", "rcs_template_id"],
    {
        "transport_task_id": _TRANSPORT_TASK_ID_SCHEMA,
        "kind": {"type": "string", "enum": ["RACK_ROTATE"]},
        "rack_id": _NONBLANK_TEXT_SCHEMA,
        "source": _RACK_POSITION_SCHEMA,
        "target": _RACK_POSITION_SCHEMA,
        "target_face": _FACE_SCHEMA,
        "rcs_template_id": _RCS_TEMPLATE_SCHEMA,
    },
    description="运行时校验 source 与 target 相同。",
)
_BIN_MOVE_DATA_SCHEMA = _closed_object(
    ["transport_task_id", "kind", "moves"],
    {
        "transport_task_id": _TRANSPORT_TASK_ID_SCHEMA,
        "kind": {"type": "string", "enum": ["BIN_MOVE"]},
        "moves": {"type": "array", "minItems": 1, "maxItems": 4, "items": _BIN_MOVE_SCHEMA},
    },
    description="运行时校验 container_id 有序且唯一、rack bin slot 唯一，并限制同 rack 仅一个 face。",
)
_BIN_EXCHANGE_DATA_SCHEMA = _closed_object(
    ["transport_task_id", "kind", "moves"],
    {
        "transport_task_id": _TRANSPORT_TASK_ID_SCHEMA,
        "kind": {"type": "string", "enum": ["BIN_EXCHANGE"]},
        "moves": {
            "type": "array",
            "minItems": 2,
            "maxItems": 4,
            "not": {"minItems": 3, "maxItems": 3},
            "items": _BIN_EXCHANGE_MOVE_SCHEMA,
        },
    },
    description="运行时校验 container_id 有序且唯一、位置唯一、每 rack 一个 face，以及二元 exchange cycle。",
)


def _submit_envelope(data_schema: dict[str, object]) -> dict[str, object]:
    return _closed_object(
        ["operation_id", "operation", "timestamp", "data"],
        {
            "operation_id": _UUIDV7_SCHEMA,
            "operation": {"type": "string", "enum": ["transport.task.submit@v1"]},
            "timestamp": _TIMESTAMP_SCHEMA,
            "data": data_schema,
        },
    )


TRANSPORT_SUBMIT_REQUEST_SCHEMA = {
    "oneOf": [
        _submit_envelope(_RACK_MOVE_DATA_SCHEMA),
        _submit_envelope(_RACK_ROTATE_DATA_SCHEMA),
        _submit_envelope(_BIN_MOVE_DATA_SCHEMA),
        _submit_envelope(_BIN_EXCHANGE_DATA_SCHEMA),
    ]
}

rack_move = {
    "operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
    "operation": "transport.task.submit@v1",
    "timestamp": 1786060800000,
    "data": {
        "transport_task_id": "transport-rack-1",
        "kind": "RACK_MOVE",
        "rack_id": "rack-1",
        "source": {"kind": "RACK_POSITION", "location_code": "buffer-a"},
        "target": {"kind": "RACK_POSITION", "location_code": "station-a"},
        "target_face": "90",
        "rcs_template_id": "F01",
    },
}
rack_rotate = {
    "operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4473",
    "operation": "transport.task.submit@v1",
    "timestamp": 1786060800001,
    "data": {
        "transport_task_id": "transport-rack-2",
        "kind": "RACK_ROTATE",
        "rack_id": "rack-2",
        "source": {"kind": "RACK_POSITION", "location_code": "station-b"},
        "target": {"kind": "RACK_POSITION", "location_code": "station-b"},
        "target_face": "270",
        "rcs_template_id": "CTU02",
    },
}
bin_move = {
    "operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4474",
    "operation": "transport.task.submit@v1",
    "timestamp": 1786060800002,
    "data": {
        "transport_task_id": "transport-bin-1",
        "kind": "BIN_MOVE",
        "moves": [
            {
                "container_id": "bin-1",
                "source": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-1", "rack_face": "90", "slot_id": "1"},
                "target": {"kind": "HANDOFF_POSITION", "location_code": "roller-in"},
            }
        ],
    },
}
bin_exchange = {
    "operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4475",
    "operation": "transport.task.submit@v1",
    "timestamp": 1786060800003,
    "data": {
        "transport_task_id": "transport-bin-2",
        "kind": "BIN_EXCHANGE",
        "moves": [
            {
                "container_id": "bin-2",
                "source": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-1", "rack_face": "90", "slot_id": "2"},
                "target": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-2", "rack_face": "90", "slot_id": "2"},
            },
            {
                "container_id": "bin-3",
                "source": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-2", "rack_face": "90", "slot_id": "2"},
                "target": {"kind": "RACK_BIN_SLOT", "rack_id": "rack-1", "rack_face": "90", "slot_id": "2"},
            },
        ],
    },
}

TRANSPORT_SUBMISSION_EXAMPLES = {
    "rack_move": {"summary": "移架", "value": rack_move},
    "rack_rotate": {"summary": "转架", "value": rack_rotate},
    "bin_move": {"summary": "移箱", "value": bin_move},
    "bin_exchange": {"summary": "换箱", "value": bin_exchange},
}


def _ack_schema(code: str, data_schema: dict[str, object]) -> dict[str, object]:
    return _closed_object(
        ["operation_id", "code", "timestamp", "data"],
        {
            "operation_id": _UUIDV7_SCHEMA,
            "code": {"type": "string", "enum": [code]},
            "timestamp": _TIMESTAMP_SCHEMA,
            "data": data_schema,
        },
    )


_TASK_ACK_DATA_SCHEMA = _closed_object(["transport_task_id"], {"transport_task_id": _TRANSPORT_TASK_ID_SCHEMA})
_REJECTED_ACK_DATA_SCHEMA = {
    "oneOf": [
        _closed_object(["reason_code"], {"reason_code": _REJECTION_REASON_SCHEMA}),
        _closed_object(
            ["transport_task_id", "reason_code"],
            {"transport_task_id": _TRANSPORT_TASK_ID_SCHEMA, "reason_code": _REJECTION_REASON_SCHEMA},
        ),
    ]
}


def _json_response(description: str, schema: dict[str, object]) -> dict[str, object]:
    return {"description": description, "content": {"application/json": {"schema": schema}}}


TRANSPORT_SUBMISSION_RESPONSES: dict[int, dict[str, Any]] = {
    200: _json_response("相同请求的幂等重放", _ack_schema("DUPLICATE", _TASK_ACK_DATA_SCHEMA)),
    202: _json_response("Transport 请求已接收", _ack_schema("RECEIVED", _TASK_ACK_DATA_SCHEMA)),
    400: {"description": "媒体类型、编码、严格 JSON 或 envelope 无效"},
    409: _json_response(
        "operation_id、transport_task_id 或活动资源冲突", _ack_schema("CONFLICT", _TASK_ACK_DATA_SCHEMA)
    ),
    413: {"description": "请求体超过 256 KiB"},
    422: _json_response("Transport 数据不满足运行时合同", _ack_schema("REJECTED", _REJECTED_ACK_DATA_SCHEMA)),
    503: _json_response("Mock 暂时不可用或缺少可信当前面", _ack_schema("UNAVAILABLE", _TASK_ACK_DATA_SCHEMA)),
}


def transport_submit_openapi_extra() -> dict[str, object]:
    return {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": deepcopy(TRANSPORT_SUBMIT_REQUEST_SCHEMA),
                    "examples": deepcopy(TRANSPORT_SUBMISSION_EXAMPLES),
                }
            },
        }
    }


member_target_placed = {
    "operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4480",
    "operation": "transport.task.member_position_changed@v1",
    "timestamp": 1786060800100,
    "data": {
        "transport_task_id": "transport-bin-1",
        "container_id": "bin-1",
        "milestone": "TARGET_PLACED",
        "final_position": {"kind": "HANDOFF_POSITION", "location_code": "roller-in"},
    },
}
rack_succeeded = {
    "operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4481",
    "operation": "transport.task.resulted@v1",
    "timestamp": 1786060800101,
    "data": {
        "transport_task_id": "transport-rack-1",
        "kind": "RACK_MOVE",
        "outcome_revision": 1,
        "rack_id": "rack-1",
        "status": "SUCCEEDED",
        "final_position": {"kind": "RACK_POSITION", "location_code": "station-a"},
        "arrival_face": "90",
    },
}
bin_succeeded = {
    "operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4482",
    "operation": "transport.task.resulted@v1",
    "timestamp": 1786060800102,
    "data": {
        "transport_task_id": "transport-bin-1",
        "kind": "BIN_MOVE",
        "outcome_revision": 1,
        "results": [
            {
                "container_id": "bin-1",
                "status": "SUCCEEDED",
                "final_position": {"kind": "HANDOFF_POSITION", "location_code": "roller-in"},
            }
        ],
    },
}

TRANSPORT_CALLBACK_EXAMPLES = {
    "member_target_placed": {"summary": "成员到达目标位置", "value": member_target_placed},
    "rack_succeeded": {"summary": "移架成功", "value": rack_succeeded},
    "bin_succeeded": {"summary": "移箱成功", "value": bin_succeeded},
}
TRANSPORT_CALLBACK_RESPONSES: dict[int, dict[str, Any]] = {
    200: {"description": "callback 已转发，status_code 为 WES 返回码"},
    502: {"description": "WES callback endpoint 不可达"},
    504: {"description": "WES callback endpoint 超时"},
}


def transport_callback_openapi_extra() -> dict[str, object]:
    return {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": transport_callback_request_schema(),
                    "examples": deepcopy(TRANSPORT_CALLBACK_EXAMPLES),
                }
            },
        }
    }


def transport_callback_request_schema() -> dict[str, object]:
    return deepcopy(TRANSPORT_EVENT_REQUEST_SCHEMA)
