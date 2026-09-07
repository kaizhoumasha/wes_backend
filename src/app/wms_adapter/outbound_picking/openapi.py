"""WMS 出库 PickingTask 入站事件的 OpenAPI 片段。"""

from __future__ import annotations

from src.app.wms_adapter.outbound_picking.queue_changed_wire import PICKING_TASK_QUEUE_CHANGED_OPERATION
from src.app.wms_adapter.outbound_picking.wire import (
    BUSINESS_IDENTIFIER_PATTERN,
    PICKING_TASK_ISSUED_OPERATION,
)
from src.app.wms_adapter.wire_common import UUIDV7_PATTERN

_UUIDV7 = {"type": "string", "pattern": UUIDV7_PATTERN}
_TIMESTAMP = {
    "type": "integer",
    "format": "int64",
    "minimum": 1,
    "maximum": 2**63 - 1,
    "description": "Unix 毫秒时间戳",
}
_NONNEGATIVE_TIMESTAMP = {**_TIMESTAMP, "minimum": 0}
_POSITIVE_INTEGER = {"type": "integer", "minimum": 1, "maximum": 2**63 - 1}
_BUSINESS_IDENTIFIER = {"type": "string", "pattern": BUSINESS_IDENTIFIER_PATTERN}


def _closed(required: list[str], properties: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


PICKING_TASK_ISSUED_EVENT_REQUEST_SCHEMA = _closed(
    ["operation_id", "operation", "timestamp", "data"],
    {
        "operation_id": _UUIDV7,
        "operation": {"type": "string", "enum": [PICKING_TASK_ISSUED_OPERATION]},
        "timestamp": _TIMESTAMP,
        "data": _closed(
            ["task_id", "task_type", "queue_revision", "dispatch_sequence"],
            {
                "task_id": _BUSINESS_IDENTIFIER,
                "task_type": {"type": "string", "enum": ["MANUAL", "AUTO"]},
                "queue_revision": {"type": "integer", "minimum": 1, "maximum": 1},
                "dispatch_sequence": _POSITIVE_INTEGER,
                "not_before": _NONNEGATIVE_TIMESTAMP,
            },
        ),
    },
)

__all__ = [
    "PICKING_TASK_ISSUED_EVENT_REQUEST_SCHEMA",
    "PICKING_TASK_PLAN_DELTA_EVENT_REQUEST_SCHEMA",
    "PICKING_TASK_QUEUE_CHANGED_EVENT_REQUEST_SCHEMA",
]

# 计划增量保持独立 schema，公开 Event route 负责静态接入。
_RACK_FACE = {"type": "string", "minLength": 1, "maxLength": 10, "pattern": r"^[^\u0000\uD800-\uDFFF]+$"}
_PLAN_RACK = _closed(["rack_id", "rack_face"], {"rack_id": _BUSINESS_IDENTIFIER, "rack_face": _RACK_FACE})
_PLAN_SLOT = _closed(
    ["type", "rack_id", "rack_face", "slot_id"],
    {
        "type": {"type": "string", "enum": ["RACK_SLOT"]},
        "rack_id": _BUSINESS_IDENTIFIER,
        "rack_face": _RACK_FACE,
        "slot_id": _BUSINESS_IDENTIFIER,
    },
)
_PLAN_DELTA_DATA = _closed(
    ["task_id", "plan_revision"],
    {
        "task_id": _BUSINESS_IDENTIFIER,
        "plan_revision": _POSITIVE_INTEGER,
        "target_rack": _PLAN_RACK,
        "added_bin_source_racks": {"type": "array", "minItems": 1, "items": _PLAN_RACK},
        "added_direct_picks": {
            "type": "array",
            "minItems": 1,
            "items": _closed(["source_locator"], {"source_locator": _PLAN_SLOT}),
        },
    },
)
_PLAN_DELTA_DATA["oneOf"] = [
    {"properties": {"plan_revision": {"const": 1}}, "required": ["target_rack"]},
    {
        "properties": {"plan_revision": {"minimum": 2}},
        "not": {"required": ["target_rack"]},
        "anyOf": [{"required": ["added_bin_source_racks"]}, {"required": ["added_direct_picks"]}],
    },
]
PICKING_TASK_PLAN_DELTA_EVENT_REQUEST_SCHEMA = _closed(
    ["operation_id", "operation", "timestamp", "data"],
    {
        "operation_id": _UUIDV7,
        "operation": {"type": "string", "enum": ["outbound.picking_task.plan_delta@v1"]},
        "timestamp": _NONNEGATIVE_TIMESTAMP,
        "data": _PLAN_DELTA_DATA,
    },
)

_QUEUE_CHANGED_DATA = _closed(
    ["task_id", "queue_revision"],
    {
        "task_id": _BUSINESS_IDENTIFIER,
        "queue_revision": {**_POSITIVE_INTEGER, "minimum": 2},
        "dispatch_sequence": _POSITIVE_INTEGER,
        "not_before": _NONNEGATIVE_TIMESTAMP,
    },
)
_QUEUE_CHANGED_DATA["anyOf"] = [{"required": ["dispatch_sequence"]}, {"required": ["not_before"]}]
PICKING_TASK_QUEUE_CHANGED_EVENT_REQUEST_SCHEMA = _closed(
    ["operation_id", "operation", "timestamp", "data"],
    {
        "operation_id": _UUIDV7,
        "operation": {"type": "string", "enum": [PICKING_TASK_QUEUE_CHANGED_OPERATION]},
        "timestamp": _NONNEGATIVE_TIMESTAMP,
        "data": _QUEUE_CHANGED_DATA,
    },
)
