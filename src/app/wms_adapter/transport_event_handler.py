"""框架无关的 WMS Transport 异步事件处理器。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from src.app.transport.contracts import TransportContractError
from src.app.wms_adapter.transport_wire import validate_callback_envelope
from src.core.uuid7 import is_uuid7
from src.utils.timezone import timezone

MAX_TRANSPORT_EVENT_BODY_BYTES = 256 * 1024

logger = logging.getLogger(__name__)


class _EvidenceRecorder(Protocol):
    async def record_evidence(
        self,
        *,
        operation_id: str,
        transport_task_id: str,
        operation: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class TransportEventResponse:
    http_status: int
    body: dict[str, Any]


class TransportEventHandler:
    def __init__(self, recorder: _EvidenceRecorder) -> None:
        self._recorder = recorder

    async def handle(self, raw_body: bytes) -> TransportEventResponse:
        if len(raw_body) > MAX_TRANSPORT_EVENT_BODY_BYTES:
            return TransportEventResponse(413, {})
        try:
            decoded = raw_body.decode("utf-8")
            raw_envelope = json.loads(decoded)
        except (UnicodeDecodeError, ValueError, RecursionError):
            return TransportEventResponse(400, {})
        operation_id = raw_envelope.get("operation_id") if isinstance(raw_envelope, dict) else None
        if not is_uuid7(operation_id):
            return TransportEventResponse(400, {})
        try:
            envelope = validate_callback_envelope(raw_envelope)
        except TransportContractError:
            return _response(422, operation_id, "REJECTED", _timestamp_ms(), {"reason_code": "INVALID_EVIDENCE"})
        data = envelope["data"]
        try:
            ack = await self._recorder.record_evidence(
                operation_id=operation_id,
                transport_task_id=data["transport_task_id"],
                operation=envelope["operation"],
                payload=data,
            )
        except Exception:
            logger.exception("Transport evidence 持久化失败: operation=%s", envelope["operation"])
            return _response(503, operation_id, "UNAVAILABLE", _timestamp_ms(), {})
        code = ack["code"]
        status = {"RECEIVED": 202, "DUPLICATE": 200, "CONFLICT": 409}[code]
        return _response(status, operation_id, code, ack["timestamp"], ack["data"])


def _response(
    status: int,
    operation_id: str,
    code: str,
    timestamp: int,
    data: dict[str, Any],
) -> TransportEventResponse:
    return TransportEventResponse(
        http_status=status,
        body={
            "operation_id": operation_id,
            "code": code,
            "timestamp": timestamp,
            "data": data,
        },
    )


def _timestamp_ms() -> int:
    return int(timezone.now_utc().timestamp() * 1000)


__all__ = ["MAX_TRANSPORT_EVENT_BODY_BYTES", "TransportEventHandler", "TransportEventResponse"]
