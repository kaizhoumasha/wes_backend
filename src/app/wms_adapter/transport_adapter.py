"""经 WMS 转发 RCS 的 Transport 适配器。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.app.transport.contracts import TransportSubmitCode, TransportSubmitResult
from src.app.transport.submit_snapshot import build_submit_envelope, submit_payload_digest
from src.app.wms_adapter.client import OutboundHttpClosedError, WmsRequestBodyTooLargeError
from src.app.wms_adapter.strict_json import is_json_utf8_media_type as _valid_json_media_type
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
        timestamp: int,
        payload: dict[str, object],
        payload_digest: str,
    ) -> TransportSubmitResult:
        envelope = build_submit_envelope(operation_id, timestamp, payload)
        transport_task_id = payload.get("transport_task_id")
        if (
            not is_uuid7(operation_id)
            or operation_id != operation_id.lower()
            or not isinstance(timestamp, int)
            or isinstance(timestamp, bool)
            or not 0 <= timestamp <= _SIGNED_INT64_MAX
            or not isinstance(transport_task_id, str)
            or not transport_task_id.strip()
            or submit_payload_digest(operation_id, timestamp, payload) != payload_digest
        ):
            return TransportSubmitResult(
                TransportSubmitCode.REJECTED,
                transport_task_id if isinstance(transport_task_id, str) else "",
                reason_code="PAYLOAD_DIGEST_MISMATCH",
            )
        try:
            access = await self._client.post(
                TRANSPORT_PATH,
                json=envelope,
                max_request_body_bytes=_BODY_LIMIT,
                max_response_body_bytes=_BODY_LIMIT,
            )
        except (WmsRequestBodyTooLargeError, OutboundHttpClosedError) as error:
            request_too_large = isinstance(error, WmsRequestBodyTooLargeError)
            return TransportSubmitResult(
                TransportSubmitCode.REJECTED if request_too_large else TransportSubmitCode.NOT_SENT,
                transport_task_id,
                reason_code="PAYLOAD_TOO_LARGE" if request_too_large else None,
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
                reason_code="PAYLOAD_TOO_LARGE" if access.status_code == 413 else "INVALID_REQUEST",
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
        retry_after_ms = data.get("retry_after_ms")
        if (
            code is not TransportSubmitCode.BUSY
            or not isinstance(retry_after_ms, int)
            or isinstance(retry_after_ms, bool)
            or not 1 <= retry_after_ms <= 60_000
        ):
            retry_after_ms = None
        return TransportSubmitResult(
            code,
            transport_task_id,
            reason_code=_persistable_reason_code(data.get("reason_code")),
            retry_after_ms=retry_after_ms,
        )


def _map_response_code(status_code: int | None, code: object) -> TransportSubmitCode:
    mapping: dict[tuple[int | None, object], TransportSubmitCode] = {
        (202, "RECEIVED"): TransportSubmitCode.RECEIVED,
        (200, "DUPLICATE"): TransportSubmitCode.DUPLICATE,
        (409, "CONFLICT"): TransportSubmitCode.CONFLICT,
        (422, "REJECTED"): TransportSubmitCode.REJECTED,
        (429, "BUSY"): TransportSubmitCode.BUSY,
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
    return isinstance(data, dict) and set(data) <= {"transport_task_id", "reason_code", "retry_after_ms"}


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
    if code is TransportSubmitCode.BUSY:
        return set(data) <= {"transport_task_id", "retry_after_ms"}
    return set(data) == {"transport_task_id"}


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
