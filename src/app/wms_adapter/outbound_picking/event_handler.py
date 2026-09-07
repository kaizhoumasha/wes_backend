"""PickingTask 发布事件的严格接收与 ACK 映射。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from src.app.wms_adapter.outbound_picking.event_ack import EventAck, event_ack
from src.app.wms_adapter.outbound_picking.wire import (
    PICKING_TASK_ISSUED_OPERATION,
    PickingTaskIssuedEvent,
    PickingTaskIssuedInvalidData,
    parse_picking_task_issued_receipt,
)
from src.app.wms_adapter.wire_common import (
    is_wire_operation,
    is_wire_operation_id,
)
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PickingTaskIssuedPersistenceResult:
    code: str
    timestamp_ms: int
    reason_code: str | None = None


class PickingTaskIssuedRecorder(Protocol):
    async def record(
        self,
        envelope: PickingTaskIssuedEvent | PickingTaskIssuedInvalidData,
        *,
        received_at: datetime,
    ) -> PickingTaskIssuedPersistenceResult: ...


class PickingTaskIssuedHandler:
    def __init__(self, recorder: PickingTaskIssuedRecorder) -> None:
        self._recorder = recorder

    async def handle(self, raw_envelope: dict[str, Any]) -> EventAck:
        operation_id = raw_envelope.get("operation_id")
        operation = raw_envelope.get("operation")
        if not is_wire_operation_id(operation_id) or not is_wire_operation(operation):
            return EventAck(400, {})
        response_time = int(timezone.now_utc().timestamp() * 1000)
        if operation != PICKING_TASK_ISSUED_OPERATION:
            return event_ack(422, operation_id, "REJECTED", response_time, {"reason_code": "UNSUPPORTED_OPERATION"})
        envelope = parse_picking_task_issued_receipt(raw_envelope)
        try:
            persisted = await self._recorder.record(envelope, received_at=timezone.now_for_db())
        except Exception:
            logger.exception("PickingTask 发布事件持久化失败: operation_id=%s", operation_id)
            return event_ack(503, operation_id, "UNAVAILABLE", response_time, {})
        if persisted.code == "RECEIVED":
            return event_ack(202, operation_id, persisted.code, persisted.timestamp_ms, {})
        if persisted.code == "DUPLICATE":
            return event_ack(200, operation_id, persisted.code, persisted.timestamp_ms, {})
        if persisted.code == "REJECTED" and persisted.reason_code == "INVALID_DATA":
            return event_ack(422, operation_id, persisted.code, persisted.timestamp_ms, {"reason_code": "INVALID_DATA"})
        if persisted.code == "CONFLICT" and persisted.reason_code in {
            "IDEMPOTENCY_CONFLICT",
            "STATE_CONFLICT",
        }:
            return event_ack(
                409, operation_id, persisted.code, persisted.timestamp_ms, {"reason_code": persisted.reason_code}
            )
        logger.error("PickingTask 发布事件持久化返回未知结果: %r", persisted)
        return event_ack(503, operation_id, "UNAVAILABLE", response_time, {})


__all__ = [
    "PickingTaskIssuedHandler",
    "PickingTaskIssuedPersistenceResult",
    "PickingTaskIssuedRecorder",
]
