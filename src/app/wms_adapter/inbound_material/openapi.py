"""共享 WMS 入站事件的 OpenAPI 片段。"""

from __future__ import annotations

from copy import deepcopy

from src.app.wms_adapter.inbound_material.wire import RECOVERY_OPERATION
from src.app.wms_adapter.outbound_picking.openapi import PICKING_TASK_EVENT_EXAMPLES
from src.app.wms_adapter.outbound_picking.response_wire import ConflictData
from src.app.wms_adapter.transport_openapi import TRANSPORT_EVENT_EXAMPLES, TRANSPORT_EVENT_RESPONSES

_IDENTIFIER = {
    "type": "string",
    "minLength": 1,
    "allOf": [
        {"not": {"pattern": r"^\s*$"}},
        {"not": {"pattern": r"\u0000"}},
    ],
}
_EXECUTION_CODE = {**_IDENTIFIER, "maxLength": 120}
_MATERIAL_TRACE_ID = {**_IDENTIFIER, "maxLength": 160}
_UUIDV7 = {
    "type": "string",
    "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
}
_TIMESTAMP = {
    "type": "integer",
    "format": "int64",
    "minimum": 1,
    "maximum": 2**63 - 1,
    "description": "Unix 毫秒时间戳",
}


def _closed(required: list[str], properties: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


_HANDOFF = _closed(
    ["type", "location_code"],
    {"type": {"type": "string", "enum": ["HANDOFF_POSITION"]}, "location_code": _IDENTIFIER},
)
_NG = _closed(
    ["type", "location_code"],
    {"type": {"type": "string", "enum": ["NG_POSITION"]}, "location_code": _IDENTIFIER},
)
_CELL = _closed(
    ["type", "rack_id", "rack_slot_code", "bin_code", "bin_cell_id"],
    {
        "type": {"type": "string", "enum": ["ONE_LAYER_BIN_CELL"]},
        "rack_id": _IDENTIFIER,
        "rack_slot_code": _IDENTIFIER,
        "bin_code": _IDENTIFIER,
        "bin_cell_id": _IDENTIFIER,
    },
)
_DATA = _closed(
    [
        "recovery_id",
        "material_execution_id",
        "material_trace_id",
        "reconciling_evidence_id",
        "decision",
        "authoritative_position",
        "reason_code",
    ],
    {
        "recovery_id": _IDENTIFIER,
        "material_execution_id": _EXECUTION_CODE,
        "material_trace_id": _MATERIAL_TRACE_ID,
        "reconciling_evidence_id": _IDENTIFIER,
        "decision": {"type": "string", "enum": ["CONTINUE", "ABORT"]},
        "authoritative_position": {"oneOf": [_HANDOFF, _NG, _CELL, {"type": "null"}]},
        "reason_code": _IDENTIFIER,
    },
)
_DATA["allOf"] = [
    {
        "if": {"properties": {"decision": {"const": "CONTINUE"}}, "required": ["decision"]},
        "then": {"properties": {"authoritative_position": {"oneOf": [_HANDOFF, _NG, _CELL]}}},
    }
]

RECOVERY_EVENT_REQUEST_SCHEMA = _closed(
    ["operation_id", "operation", "timestamp", "data"],
    {
        "operation_id": _UUIDV7,
        "operation": {"type": "string", "enum": [RECOVERY_OPERATION]},
        "timestamp": _TIMESTAMP,
        "data": _DATA,
    },
)

_EMPTY_DATA = _closed([], {})
_INBOUND_REJECTION_DATA = _closed(
    ["reason_code"],
    {
        "reason_code": {
            "type": "string",
            "enum": ["INVALID_DATA", "UNSUPPORTED_OPERATION"],
        }
    },
)


WMS_EVENT_EXAMPLES = {
    **PICKING_TASK_EVENT_EXAMPLES,
    **TRANSPORT_EVENT_EXAMPLES,
    "07_inbound_recovery": {
        "summary": "7. 入库对账确认后继续执行",
        "description": "引用实际 RECONCILING 执行与证据；authoritative_position 必须来自已核实的位置事实，不能用该示例创建恢复条件。",
        "value": {
            "operation_id": "019f3400-0e17-7d2a-b944-3cf7953804e1",
            "operation": RECOVERY_OPERATION,
            "timestamp": 1786060806000,
            "data": {
                "recovery_id": "REC-SWAGGER-001",
                "material_execution_id": "EXEC-SWAGGER-001",
                "material_trace_id": "TRACE-SWAGGER-001",
                "reconciling_evidence_id": "31",
                "decision": "CONTINUE",
                "authoritative_position": {"type": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
                "reason_code": "MANUAL_CONFIRMED",
            },
        },
    },
}


def _combined_event_responses() -> dict[int | str, dict[str, object]]:
    responses = deepcopy(TRANSPORT_EVENT_RESPONSES)
    for status in (200, 202):
        schema = responses[status]["content"]["application/json"]["schema"]
        transport_data = schema["properties"]["data"]
        schema["properties"]["data"] = {"oneOf": [transport_data, _EMPTY_DATA]}
    rejected_schema = responses[422]["content"]["application/json"]["schema"]
    rejected_schema["properties"]["data"]["oneOf"].append(_INBOUND_REJECTION_DATA)
    conflict_schema = responses[409]["content"]["application/json"]["schema"]
    conflict_schema["properties"]["data"]["oneOf"].append(ConflictData.model_json_schema())
    for status, code, data in (
        (409, "CONFLICT", {"reason_code": "IDEMPOTENCY_CONFLICT"}),
        (422, "REJECTED", {"reason_code": "INVALID_DATA"}),
        (503, "UNAVAILABLE", {}),
    ):
        responses[status]["content"]["application/json"]["examples"] = {
            "picking_task": {
                "summary": f"PickingTask {code}（operation_id 匹配请求）",
                "value": {
                    "operation_id": "019f3400-0e17-7d2a-b944-3cf7953804da",
                    "code": code,
                    "timestamp": 1786060800100,
                    "data": data,
                },
            }
        }
    for status, code in ((202, "RECEIVED"), (200, "DUPLICATE")):
        responses[status]["content"]["application/json"]["examples"] = {
            key: {
                "summary": f"{example['summary']} → {status} / {code}",
                "description": "operation_id 与对应请求一致；timestamp 为示例首次接收时间，重放沿用首次时间。",
                "value": {
                    "operation_id": example["value"]["operation_id"],
                    "code": code,
                    "timestamp": example["value"]["timestamp"] + 100,
                    "data": (
                        {"transport_task_id": example["value"]["data"]["transport_task_id"]}
                        if "transport_task_id" in example["value"]["data"]
                        else {}
                    ),
                },
            }
            for key, example in WMS_EVENT_EXAMPLES.items()
        }
    responses[200]["description"] = "相同 WMS event 已可靠持久化"
    responses[202]["description"] = "WMS event 已可靠持久化"
    responses[409]["description"] = "WMS event 身份、内容或不可变事实冲突"
    responses[422]["description"] = "WMS event 信封或 operation 专属 data 不合法"
    responses[503]["description"] = "对应 WMS event runtime 未就绪或无法可靠持久化"
    return responses


WMS_EVENT_RESPONSES = _combined_event_responses()

__all__ = ["RECOVERY_EVENT_REQUEST_SCHEMA", "WMS_EVENT_EXAMPLES", "WMS_EVENT_RESPONSES"]
