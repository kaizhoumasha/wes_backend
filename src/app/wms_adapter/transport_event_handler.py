"""框架无关的 WMS Transport 异步事件处理器。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol, TypeGuard, cast

from src.app.transport.contracts import TransportContractError
from src.app.wms_adapter.strict_json import StrictJsonError, loads_transport_json
from src.app.wms_adapter.transport_wire import UnsupportedTransportOperation, validate_callback_envelope
from src.core.uuid7 import is_uuid7
from src.utils.timezone import timezone

MAX_TRANSPORT_EVENT_BODY_BYTES = 256 * 1024

logger = logging.getLogger(__name__)


class _EvidenceRecorder(Protocol):
    async def record_callback(
        self,
        *,
        operation_id: str,
        operation: str,
        message: dict[str, Any],
        payload: dict[str, Any] | None,
        rejection_reason_code: str | None,
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
        raw_envelope, associated_parse_error, parsing_error = _decode_raw_envelope(raw_body)
        if parsing_error is not None:
            return parsing_error
        if raw_envelope is None:
            return TransportEventResponse(400, {})
        operation_id = raw_envelope["operation_id"]
        operation = raw_envelope["operation"]
        envelope: dict[str, Any] | None = None
        rejection_reason_code: str | None = "INVALID_EVIDENCE" if associated_parse_error else None
        if not associated_parse_error:
            try:
                envelope = validate_callback_envelope(raw_envelope)
            except UnsupportedTransportOperation:
                rejection_reason_code = "UNSUPPORTED_OPERATION"
            except TransportContractError:
                rejection_reason_code = "INVALID_EVIDENCE"
        try:
            ack = await self._recorder.record_callback(
                operation_id=operation_id,
                operation=operation,
                message=raw_envelope,
                payload=envelope["data"] if envelope is not None else None,
                rejection_reason_code=rejection_reason_code,
            )
        except Exception:
            logger.exception("Transport callback 持久化失败: operation=%s", operation)
            return _response(503, operation_id, "UNAVAILABLE", _timestamp_ms(), {})
        return _response(ack["http_status"], operation_id, ack["code"], ack["timestamp"], ack["data"])


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


def _decode_raw_envelope(
    raw_body: bytes,
) -> tuple[dict[str, Any] | None, bool, TransportEventResponse | None]:
    try:
        decoded = raw_body.decode("utf-8")
    except UnicodeDecodeError:
        return None, False, TransportEventResponse(400, {})
    try:
        value = loads_transport_json(decoded)
    except StrictJsonError as error:
        if error.duplicate_key:
            return None, False, TransportEventResponse(400, {})
        operation_id = error.operation_id
        operation = error.operation
        if not _is_wire_operation_id(operation_id) or not _is_wire_operation(operation):
            return None, False, TransportEventResponse(400, {})
        return (
            {
                "operation_id": operation_id,
                "operation": operation,
                "raw_request_body": decoded,
            },
            True,
            None,
        )
    if not isinstance(value, dict):
        return None, False, TransportEventResponse(400, {})
    envelope = cast("dict[str, Any]", value)
    operation_id = envelope.get("operation_id")
    operation = envelope.get("operation")
    if not _is_wire_operation_id(operation_id) or not _is_wire_operation(operation):
        return None, False, TransportEventResponse(400, {})
    return envelope, False, None


def _is_wire_operation_id(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and value == value.lower() and is_uuid7(value)


def _is_wire_operation(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 80


__all__ = ["MAX_TRANSPORT_EVENT_BODY_BYTES", "TransportEventHandler", "TransportEventResponse"]
