"""WMS 出库 PickingTask 入站事件的 OpenAPI 片段。"""

from __future__ import annotations

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

__all__ = ["PICKING_TASK_ISSUED_EVENT_REQUEST_SCHEMA"]
