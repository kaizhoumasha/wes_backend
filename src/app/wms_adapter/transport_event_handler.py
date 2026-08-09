"""框架无关的 WMS Transport 异步事件处理器。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from src.app.transport.contracts import TransportContractError
from src.app.wms_adapter.transport_wire import validate_callback_envelope
from src.utils.timezone import timezone

MAX_TRANSPORT_EVENT_BODY_BYTES = 256 * 1024


class _EvidenceRecorder(Protocol):
    async def record_evidence(
        self,
        *,
        event_id: str,
        transport_task_id: str,
        operation: str,
        payload: dict[str, Any],
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class TransportEventResponse:
    http_status: int
    body: dict[str, Any]


class TransportEventHandler:
    def __init__(self, recorder: _EvidenceRecorder) -> None:
        self._recorder = recorder

    async def handle(self, raw_body: bytes) -> TransportEventResponse:
        if len(raw_body) > MAX_TRANSPORT_EVENT_BODY_BYTES:
            return _response(413, None, "PAYLOAD_TOO_LARGE", "payload too large")
        try:
            decoded = raw_body.decode("utf-8")
            raw_envelope = json.loads(decoded)
        except (UnicodeDecodeError, ValueError, RecursionError):
            return _response(400, None, "REJECTED", "invalid UTF-8 JSON")
        try:
            envelope = validate_callback_envelope(raw_envelope)
        except TransportContractError as error:
            request_id = raw_envelope.get("request_id") if isinstance(raw_envelope, dict) else None
            return _response(422, request_id if isinstance(request_id, str) else None, "REJECTED", str(error))
        data = envelope["data"]
        code = await self._recorder.record_evidence(
            event_id=data["event_id"],
            transport_task_id=data["transport_task_id"],
            operation=envelope["operation"],
            payload=data,
        )
        status = {"RECEIVED": 202, "DUPLICATE": 200, "CONFLICT": 409}[code]
        return _response(status, envelope["request_id"], code, code.lower())


def _response(status: int, request_id: str | None, code: str, message: str) -> TransportEventResponse:
    return TransportEventResponse(
        http_status=status,
        body={
            "request_id": request_id,
            "code": code,
            "message": message,
            "timestamp": int(timezone.now_utc().timestamp() * 1000),
            "data": {},
        },
    )


__all__ = ["MAX_TRANSPORT_EVENT_BODY_BYTES", "TransportEventHandler", "TransportEventResponse"]
