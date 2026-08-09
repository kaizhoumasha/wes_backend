"""经 WMS 转发 RCS 的 Transport 适配器。"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from src.app.transport.contracts import TransportSubmitCode, TransportSubmitResult
from src.app.wms_adapter.client import WmsRequestBodyTooLargeError
from src.app.wms_adapter.transport_wire import SUBMIT_OPERATION, TRANSPORT_PATH, build_submit_data
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from src.app.transport.contracts import TransportRequest
    from src.app.wms_adapter.client import WmsClient

_BODY_LIMIT = 256 * 1024


class WmsTransportAdapter:
    """把一个类型化搬运请求转换成一次固定 WMS 调用。"""

    def __init__(self, client: WmsClient) -> None:
        self._client = client

    async def submit(self, request: TransportRequest, *, transport_task_id: str) -> TransportSubmitResult:
        request_id = str(uuid.uuid4())
        envelope = {
            "request_id": request_id,
            "operation": SUBMIT_OPERATION,
            "timestamp": int(timezone.now_utc().timestamp() * 1000),
            "data": build_submit_data(request, transport_task_id),
        }
        try:
            access = await self._client.post(
                TRANSPORT_PATH,
                json=envelope,
                max_request_body_bytes=_BODY_LIMIT,
                max_response_body_bytes=_BODY_LIMIT,
            )
        except WmsRequestBodyTooLargeError:
            return TransportSubmitResult(TransportSubmitCode.NOT_SENT, transport_task_id)
        delivery_state = getattr(access.delivery_state, "value", access.delivery_state)
        if delivery_state == "NOT_SENT":
            return TransportSubmitResult(TransportSubmitCode.NOT_SENT, transport_task_id)
        if delivery_state != "RESPONSE_RECEIVED":
            return TransportSubmitResult(TransportSubmitCode.DELIVERY_UNKNOWN, transport_task_id)
        body = access.json_body
        if access.json_failure is not None or not isinstance(body, dict):
            return TransportSubmitResult(TransportSubmitCode.DELIVERY_UNKNOWN, transport_task_id)
        if not _valid_ack_envelope(body, request_id):
            return TransportSubmitResult(TransportSubmitCode.DELIVERY_UNKNOWN, transport_task_id)
        code = _map_response_code(access.status_code, body.get("code"))
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        if data.get("transport_task_id") != transport_task_id:
            return TransportSubmitResult(TransportSubmitCode.CONFLICT, transport_task_id)
        return TransportSubmitResult(
            code,
            transport_task_id,
            reason_code=data.get("reason_code") if isinstance(data.get("reason_code"), str) else None,
            retry_after_ms=data.get("retry_after_ms") if isinstance(data.get("retry_after_ms"), int) else None,
        )


def _map_response_code(status_code: int | None, code: object) -> TransportSubmitCode:
    mapping = {
        (202, "RECEIVED"): TransportSubmitCode.RECEIVED,
        (200, "DUPLICATE"): TransportSubmitCode.DUPLICATE,
        (409, "CONFLICT"): TransportSubmitCode.CONFLICT,
        (400, "REJECTED"): TransportSubmitCode.REJECTED,
        (422, "REJECTED"): TransportSubmitCode.REJECTED,
        (429, "BUSY"): TransportSubmitCode.BUSY,
        (503, "UNAVAILABLE"): TransportSubmitCode.UNAVAILABLE,
    }
    return mapping.get((status_code, code), TransportSubmitCode.DELIVERY_UNKNOWN)


def _valid_ack_envelope(body: dict[str, object], request_id: str) -> bool:
    if set(body) != {"request_id", "code", "message", "timestamp", "data"}:
        return False
    if body.get("request_id") != request_id or not isinstance(body.get("message"), str):
        return False
    timestamp = body.get("timestamp")
    if not isinstance(timestamp, int) or isinstance(timestamp, bool):
        return False
    data = body.get("data")
    return isinstance(data, dict) and set(data) <= {"transport_task_id", "reason_code", "retry_after_ms"}


__all__ = ["WmsTransportAdapter"]
