"""PickingTask 发布事件的严格接收与 ACK 映射。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast

from src.app.wms_adapter.outbound_picking.wire import (
    PICKING_TASK_ISSUED_OPERATION,
    PickingTaskIssuedEvent,
    parse_picking_task_issued_event,
)
from src.app.wms_adapter.strict_json import StrictJsonError, loads_transport_json
from src.app.wms_adapter.wire_common import (
    MAX_WMS_EVENT_BODY_BYTES,
    is_wire_operation,
    is_wire_operation_id,
)
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PickingTaskIssuedResponse:
    http_status: int
    body: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PickingTaskIssuedPersistenceResult:
    code: str
    timestamp_ms: int
    reason_code: str | None = None


class PickingTaskIssuedRecorder(Protocol):
    async def record(
        self,
        envelope: PickingTaskIssuedEvent,
        *,
        received_at: datetime,
    ) -> PickingTaskIssuedPersistenceResult: ...


class PickingTaskIssuedHandler:
    def __init__(self, recorder: PickingTaskIssuedRecorder) -> None:
        self._recorder = recorder

    async def handle(self, raw_body: bytes) -> PickingTaskIssuedResponse:  # noqa: PLR0911
        if len(raw_body) > MAX_WMS_EVENT_BODY_BYTES:
            return PickingTaskIssuedResponse(413, {})
        try:
            raw_value = loads_transport_json(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, StrictJsonError):
            return PickingTaskIssuedResponse(400, {})
        if not isinstance(raw_value, dict):
            return PickingTaskIssuedResponse(400, {})
        raw_envelope = cast("dict[str, Any]", raw_value)
        operation_id = raw_envelope.get("operation_id")
        operation = raw_envelope.get("operation")
        if not is_wire_operation_id(operation_id) or not is_wire_operation(operation):
            return PickingTaskIssuedResponse(400, {})
        response_time = timezone.now_utc()
        if operation != PICKING_TASK_ISSUED_OPERATION:
            return _ack(422, operation_id, "REJECTED", response_time, {"reason_code": "UNSUPPORTED_OPERATION"})
        try:
            envelope = parse_picking_task_issued_event(raw_envelope)
        except (TypeError, ValueError):
            return _ack(422, operation_id, "REJECTED", response_time, {"reason_code": "INVALID_DATA"})
        try:
            persisted = await self._recorder.record(envelope, received_at=timezone.now_for_db())
        except Exception:
            logger.exception("PickingTask 发布事件持久化失败: operation_id=%s", operation_id)
            return _ack(503, operation_id, "UNAVAILABLE", response_time, {})
        if persisted.code == "RECEIVED":
            return _persisted_ack(202, operation_id, persisted, {})
        if persisted.code == "DUPLICATE":
            return _persisted_ack(200, operation_id, persisted, {})
        if persisted.code == "CONFLICT" and persisted.reason_code in {
            "IDEMPOTENCY_CONFLICT",
            "STATE_CONFLICT",
        }:
            return _persisted_ack(409, operation_id, persisted, {"reason_code": persisted.reason_code})
        logger.error("PickingTask 发布事件持久化返回未知结果: %r", persisted)
        return _ack(503, operation_id, "UNAVAILABLE", response_time, {})


def _persisted_ack(
    http_status: int,
    operation_id: str,
    persisted: PickingTaskIssuedPersistenceResult,
    data: dict[str, Any],
) -> PickingTaskIssuedResponse:
    return PickingTaskIssuedResponse(
        http_status,
        {
            "operation_id": operation_id,
            "code": persisted.code,
            "timestamp": persisted.timestamp_ms,
            "data": data,
        },
    )


def _ack(
    http_status: int,
    operation_id: str,
    code: str,
    at: datetime,
    data: dict[str, Any],
) -> PickingTaskIssuedResponse:
    return PickingTaskIssuedResponse(
        http_status,
        {
            "operation_id": operation_id,
            "code": code,
            "timestamp": int(at.timestamp() * 1000),
            "data": data,
        },
    )


__all__ = [
    "PickingTaskIssuedHandler",
    "PickingTaskIssuedPersistenceResult",
    "PickingTaskIssuedRecorder",
    "PickingTaskIssuedResponse",
]
