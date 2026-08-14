"""WMS Transport evidence 唯一生产入口。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, TypeGuard, cast

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from src.app.transport.contracts import TransportContractError
from src.app.wms_adapter.inbound_auth import WmsInboundAuthPolicy
from src.app.wms_adapter.strict_json import StrictJsonError, is_json_utf8_media_type, loads_strict_json
from src.app.wms_adapter.transport_event_handler import MAX_TRANSPORT_EVENT_BODY_BYTES
from src.app.wms_adapter.transport_openapi import TRANSPORT_EVENT_REQUEST_SCHEMA, TRANSPORT_EVENT_RESPONSES
from src.app.wms_adapter.transport_wire import UnsupportedTransportOperation, validate_callback_envelope
from src.core.task_queue_gateway import task_queue_gateway
from src.core.uuid7 import is_uuid7
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from src.app.transport.composition import TransportRuntime

logger = logging.getLogger(__name__)
router = APIRouter()


async def _read_bounded_body(request: Request) -> bytes | None:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_TRANSPORT_EVENT_BODY_BYTES:
            return None
        body.extend(chunk)
    raw_body = bytes(body)
    request._body = raw_body  # pyright: ignore[reportPrivateUsage]  # 单次有界读取后供同一 Request 复用。
    return raw_body


def _permits_transport_endpoint(policy: object) -> bool:
    return isinstance(policy, WmsInboundAuthPolicy) and policy.allows_unsigned_wms_callbacks


def _unavailable_ack(raw_body: bytes) -> JSONResponse | Response:
    try:
        decoded = raw_body.decode("utf-8")
    except UnicodeDecodeError:
        return Response(status_code=400)
    try:
        raw_envelope = loads_strict_json(decoded)
    except StrictJsonError as error:
        operation_id = error.operation_id
        if not _is_wire_operation_id(operation_id):
            return Response(status_code=400)
        return _rejected_ack(operation_id, "INVALID_EVIDENCE")
    if not isinstance(raw_envelope, dict):
        return Response(status_code=400)
    envelope = cast("dict[str, Any]", raw_envelope)
    operation_id = envelope.get("operation_id")
    if not _is_wire_operation_id(operation_id):
        return Response(status_code=400)
    try:
        validate_callback_envelope(envelope)
    except UnsupportedTransportOperation:
        return _rejected_ack(operation_id, "UNSUPPORTED_OPERATION")
    except TransportContractError:
        return _rejected_ack(operation_id, "INVALID_EVIDENCE")
    return JSONResponse(
        status_code=503,
        content={
            "operation_id": operation_id,
            "code": "UNAVAILABLE",
            "timestamp": int(timezone.now_utc().timestamp() * 1000),
            "data": {},
        },
    )


def _rejected_ack(operation_id: str, reason_code: str) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "operation_id": operation_id,
            "code": "REJECTED",
            "timestamp": int(timezone.now_utc().timestamp() * 1000),
            "data": {"reason_code": reason_code},
        },
    )


def _is_wire_operation_id(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and value == value.lower() and is_uuid7(value)


def _valid_transport_request_headers(request: Request) -> bool:
    content_types = request.headers.getlist("content-type")
    if len(content_types) != 1 or not is_json_utf8_media_type(content_types[0]):
        return False
    content_encodings = request.headers.getlist("content-encoding")
    return len(content_encodings) <= 1 and (
        not content_encodings or content_encodings[0].strip().casefold() == "identity"
    )


@router.post(
    "/events",
    responses=TRANSPORT_EVENT_RESPONSES,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": TRANSPORT_EVENT_REQUEST_SCHEMA}},
        }
    },
)
async def receive_transport_event(request: Request) -> Response:
    if not _valid_transport_request_headers(request):
        return Response(status_code=400)
    raw_body = await _read_bounded_body(request)
    if raw_body is None:
        return Response(status_code=413)

    policy = getattr(request.app.state, "wms_inbound_auth_policy", None)
    if not _permits_transport_endpoint(policy):
        return Response(status_code=401)

    runtime: TransportRuntime | None = getattr(request.app.state, "transport_runtime", None)
    if runtime is None:
        return _unavailable_ack(raw_body)
    result = await runtime.handler.handle(raw_body)
    if result.body:
        response: Response = JSONResponse(status_code=result.http_status, content=result.body)
    else:
        response = Response(status_code=result.http_status)

    if result.body.get("code") in {"RECEIVED", "DUPLICATE"}:
        try:
            task_queue_gateway.enqueue_transport_evidence()
        except Exception:
            logger.warning(
                "transport.evidence.enqueue_failed",
                extra={"event": "transport.evidence.enqueue_failed"},
                exc_info=True,
            )
    return response


__all__ = ["router"]
