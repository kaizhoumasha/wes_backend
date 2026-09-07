"""PickingTask 事件接收的公共 ACK 表示与封闭映射。"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class EventAck:
    http_status: int
    body: dict[str, Any]


def event_ack(
    http_status: int,
    operation_id: str,
    code: str,
    timestamp_ms: int,
    data: dict[str, Any],
) -> EventAck:
    return EventAck(
        http_status,
        {"operation_id": operation_id, "code": code, "timestamp": timestamp_ms, "data": data},
    )


def persisted_event_ack(operation_id: str, code: str, timestamp_ms: int, reason_code: str | None) -> EventAck | None:
    status = {"RECEIVED": 202, "DUPLICATE": 200, "UNAVAILABLE": 503}.get(code)
    if status is not None:
        return event_ack(status, operation_id, code, timestamp_ms, {})
    if code == "REJECTED" and reason_code == "INVALID_DATA":
        return event_ack(422, operation_id, code, timestamp_ms, {"reason_code": reason_code})
    if code == "CONFLICT" and reason_code in {
        "IDEMPOTENCY_CONFLICT",
        "STATE_CONFLICT",
        "REVISION_CONFLICT",
        "REFERENCE_CONFLICT",
    }:
        return event_ack(409, operation_id, code, timestamp_ms, {"reason_code": reason_code})
    return None
