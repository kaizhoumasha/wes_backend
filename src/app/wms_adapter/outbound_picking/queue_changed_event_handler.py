"""PickingTask 队列更新事件的严格接收与 ACK 映射。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from src.app.wms_adapter.outbound_picking.event_ack import EventAck, event_ack, persisted_event_ack
from src.app.wms_adapter.outbound_picking.queue_changed_wire import (
    PICKING_TASK_QUEUE_CHANGED_OPERATION,
    PickingTaskQueueChangedEvent,
    PickingTaskQueueChangedInvalidData,
    parse_picking_task_queue_changed_receipt,
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
class PickingTaskQueueChangedPersistenceResult:
    code: str
    timestamp_ms: int
    reason_code: str | None = None


class PickingTaskQueueChangedRecorder(Protocol):
    async def record(
        self,
        envelope: PickingTaskQueueChangedEvent | PickingTaskQueueChangedInvalidData,
        *,
        received_at: datetime,
    ) -> PickingTaskQueueChangedPersistenceResult: ...


class PickingTaskQueueChangedHandler:
    def __init__(self, recorder: PickingTaskQueueChangedRecorder) -> None:
        self._recorder = recorder

    async def handle(self, raw_envelope: dict[str, Any]) -> EventAck:
        operation_id = raw_envelope.get("operation_id")
        operation = raw_envelope.get("operation")
        if not is_wire_operation_id(operation_id) or not is_wire_operation(operation):
            return EventAck(400, {})
        response_timestamp = int(timezone.now_utc().timestamp() * 1000)
        if operation != PICKING_TASK_QUEUE_CHANGED_OPERATION:
            return event_ack(
                422, operation_id, "REJECTED", response_timestamp, {"reason_code": "UNSUPPORTED_OPERATION"}
            )
        envelope = parse_picking_task_queue_changed_receipt(raw_envelope)
        try:
            persisted = await self._recorder.record(envelope, received_at=timezone.now_for_db())
        except Exception:
            logger.exception("PickingTask 队列更新事件持久化失败: operation_id=%s", operation_id)
            return event_ack(503, operation_id, "UNAVAILABLE", response_timestamp, {})
        response = persisted_event_ack(operation_id, persisted.code, persisted.timestamp_ms, persisted.reason_code)
        if response is not None:
            return response
        logger.error("PickingTask 队列更新事件持久化返回未知结果: %r", persisted)
        return event_ack(503, operation_id, "UNAVAILABLE", response_timestamp, {})


__all__ = [
    "PickingTaskQueueChangedHandler",
    "PickingTaskQueueChangedPersistenceResult",
    "PickingTaskQueueChangedRecorder",
]
