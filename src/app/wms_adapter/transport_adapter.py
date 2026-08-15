"""经 WMS 转发 RCS 的 Transport 适配器。"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from src.app.transport.contracts import TransportSubmitCode, TransportSubmitResult
from src.app.transport.submit_snapshot import SUBMIT_OPERATION
from src.app.wms_adapter.client import OutboundHttpClosedError, WmsRequestBodyTooLargeError
from src.app.wms_adapter.strict_json import (
    StrictJsonError,
    loads_transport_json,
)
from src.app.wms_adapter.strict_json import (
    is_json_utf8_media_type as _valid_json_media_type,
)
from src.app.wms_adapter.transport_wire import TRANSPORT_PATH
from src.core.uuid7 import is_uuid7

if TYPE_CHECKING:
    from collections.abc import Mapping

    from src.app.wms_adapter.client import WmsClient

_BODY_LIMIT = 256 * 1024
_SIGNED_INT64_MAX = 2**63 - 1
_REJECTED_REASON_CODES = frozenset(
    {"INVALID_ENVELOPE", "UNSUPPORTED_OPERATION", "INVALID_DATA", "COORDINATED_BIN_EXCHANGE_UNSUPPORTED"}
)


class WmsTransportAdapter:
    """把一个类型化搬运请求转换成一次固定 WMS 调用。"""

    def __init__(self, client: WmsClient) -> None:
        self._client = client

    async def submit(
        self,
        *,
        operation_id: str,
        transport_task_id: str,
        request_body: bytes,
        request_body_digest: str,
    ) -> TransportSubmitResult:
        envelope = _decode_frozen_request_body(request_body)
        payload = envelope.get("data") if envelope is not None else None
        timestamp = envelope.get("timestamp") if envelope is not None else None
        if not _valid_frozen_identity(
            envelope,
            operation_id=operation_id,
            timestamp=timestamp,
            transport_task_id=transport_task_id,
            payload=payload,
            request_body=request_body,
            request_body_digest=request_body_digest,
        ):
            return TransportSubmitResult(
                TransportSubmitCode.REJECTED,
                transport_task_id,
                reason_code="REQUEST_BODY_DIGEST_MISMATCH",
            )
        try:
            access = await self._client.post_json_bytes(
                TRANSPORT_PATH,
                body=request_body,
                max_request_body_bytes=_BODY_LIMIT,
                max_response_body_bytes=_BODY_LIMIT,
            )
        except (WmsRequestBodyTooLargeError, OutboundHttpClosedError) as error:
            request_too_large = isinstance(error, WmsRequestBodyTooLargeError)
            return TransportSubmitResult(
                TransportSubmitCode.REJECTED if request_too_large else TransportSubmitCode.NOT_SENT,
                transport_task_id,
                reason_code="REQUEST_BODY_TOO_LARGE" if request_too_large else None,
            )
        delivery_state = getattr(access.delivery_state, "value", access.delivery_state)
        if delivery_state != "RESPONSE_RECEIVED":
            code = (
                TransportSubmitCode.NOT_SENT if delivery_state == "NOT_SENT" else TransportSubmitCode.DELIVERY_UNKNOWN
            )
            return TransportSubmitResult(code, transport_task_id)
        if access.status_code in {400, 413} and access.body_present is False:
            return TransportSubmitResult(
                TransportSubmitCode.REJECTED,
                transport_task_id,
                reason_code="REQUEST_BODY_TOO_LARGE" if access.status_code == 413 else "INVALID_REQUEST",
            )
        body = access.json_body
        if (
            not _valid_json_response_headers(getattr(access, "response_headers", ()))
            or access.json_failure is not None
            or not isinstance(body, dict)
            or not _valid_ack_envelope(body, operation_id)
        ):
            return TransportSubmitResult(TransportSubmitCode.DELIVERY_UNKNOWN, transport_task_id)
        code = _map_response_code(access.status_code, body.get("code"))
        data = body.get("data")
        if not isinstance(data, dict) or not _valid_ack_data(data, code):
            return TransportSubmitResult(TransportSubmitCode.DELIVERY_UNKNOWN, transport_task_id)
        acknowledged_task_id = data.get("transport_task_id")
        if acknowledged_task_id is not None and acknowledged_task_id != transport_task_id:
            return TransportSubmitResult(TransportSubmitCode.DELIVERY_UNKNOWN, transport_task_id)
        return TransportSubmitResult(
            code,
            transport_task_id,
            reason_code=_persistable_reason_code(data.get("reason_code")),
        )


def _map_response_code(status_code: int | None, code: object) -> TransportSubmitCode:
    mapping: dict[tuple[int | None, object], TransportSubmitCode] = {
        (202, "RECEIVED"): TransportSubmitCode.RECEIVED,
        (200, "DUPLICATE"): TransportSubmitCode.DUPLICATE,
        (409, "CONFLICT"): TransportSubmitCode.CONFLICT,
        (422, "REJECTED"): TransportSubmitCode.REJECTED,
        (503, "UNAVAILABLE"): TransportSubmitCode.UNAVAILABLE,
    }
    return mapping.get((status_code, code), TransportSubmitCode.DELIVERY_UNKNOWN)


def _valid_ack_envelope(body: Mapping[str, object], operation_id: str) -> bool:
    if set(body) != {"operation_id", "code", "timestamp", "data"}:
        return False
    if body.get("operation_id") != operation_id or not isinstance(body.get("code"), str):
        return False
    timestamp = body.get("timestamp")
    if not isinstance(timestamp, int) or isinstance(timestamp, bool) or not 0 <= timestamp <= _SIGNED_INT64_MAX:
        return False
    data = body.get("data")
    return isinstance(data, dict) and set(data) <= {"transport_task_id", "reason_code"}


def _valid_ack_data(data: Mapping[str, object], code: TransportSubmitCode) -> bool:
    if code is TransportSubmitCode.DELIVERY_UNKNOWN:
        return False
    if code is TransportSubmitCode.REJECTED:
        if set(data) not in ({"reason_code"}, {"transport_task_id", "reason_code"}):
            return False
        task_id = data.get("transport_task_id")
        return ("transport_task_id" not in data or _valid_task_id(task_id)) and _persistable_reason_code(
            data.get("reason_code")
        ) is not None
    task_id = data.get("transport_task_id")
    if not _valid_task_id(task_id):
        return False
    return set(data) == {"transport_task_id"}


def _decode_frozen_request_body(request_body: bytes) -> dict[str, object] | None:
    try:
        value = loads_transport_json(request_body.decode("utf-8"))
    except (UnicodeDecodeError, StrictJsonError):
        return None
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        return None
    return value


def _valid_frozen_identity(
    envelope: dict[str, object] | None,
    *,
    operation_id: str,
    timestamp: object,
    transport_task_id: str,
    payload: object,
    request_body: bytes,
    request_body_digest: str,
) -> bool:
    return bool(
        envelope is not None
        and set(envelope) == {"operation_id", "operation", "timestamp", "data"}
        and is_uuid7(operation_id)
        and operation_id == operation_id.lower()
        and envelope.get("operation_id") == operation_id
        and envelope.get("operation") == SUBMIT_OPERATION
        and isinstance(timestamp, int)
        and not isinstance(timestamp, bool)
        and 0 <= timestamp <= _SIGNED_INT64_MAX
        and isinstance(transport_task_id, str)
        and transport_task_id.strip()
        and isinstance(payload, dict)
        and payload.get("transport_task_id") == transport_task_id
        and hashlib.sha256(request_body).hexdigest() == request_body_digest
    )


def _persistable_reason_code(value: object) -> str | None:
    if not isinstance(value, str) or value not in _REJECTED_REASON_CODES:
        return None
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return None
    return value


def _valid_task_id(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 80


def _valid_json_response_headers(headers: object) -> bool:
    if not isinstance(headers, (tuple, list)):
        return False
    content_types = [value for name, value in headers if name.casefold() == "content-type"]
    if len(content_types) != 1 or not _valid_json_media_type(content_types[0]):
        return False
    encodings = [value for name, value in headers if name.casefold() == "content-encoding"]
    return len(encodings) <= 1 and (not encodings or encodings[0].strip().casefold() == "identity")


__all__ = ["WmsTransportAdapter"]
