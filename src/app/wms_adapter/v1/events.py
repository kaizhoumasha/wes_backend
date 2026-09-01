"""共享 WMS Event 唯一生产入口。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from starlette.background import BackgroundTask

from src.app.sys.services.event_stream_service import TRANSPORT_EVIDENCE_STREAM_CHANNEL, event_stream_service
from src.app.transport.contracts import (
    TRANSPORT_POSITION_OPERATION,
    TRANSPORT_RESULT_OPERATION,
    TransportIngressAttempt,
    TransportIngressDisposition,
)
from src.app.wms_adapter.inbound_auth import WmsInboundAuthPolicy
from src.app.wms_adapter.inbound_openapi import RECOVERY_EVENT_REQUEST_SCHEMA, WMS_EVENT_RESPONSES
from src.app.wms_adapter.inbound_wire import RECOVERY_OPERATION
from src.app.wms_adapter.strict_json import StrictJsonError, is_json_utf8_media_type, loads_transport_json
from src.app.wms_adapter.transport_event_handler import (
    MAX_TRANSPORT_EVENT_BODY_BYTES,
    is_wire_operation,
    is_wire_operation_id,
)
from src.app.wms_adapter.transport_openapi import TRANSPORT_EVENT_REQUEST_SCHEMA
from src.core.task_queue_gateway import task_queue_gateway
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from src.app.transport.composition import TransportRuntime

logger = logging.getLogger(__name__)
router = APIRouter()
WMS_EVENT_REQUEST_SCHEMA = {"oneOf": [*TRANSPORT_EVENT_REQUEST_SCHEMA["oneOf"], RECOVERY_EVENT_REQUEST_SCHEMA]}


async def _read_bounded_body(request: Request) -> tuple[bytes | None, int]:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_TRANSPORT_EVENT_BODY_BYTES:
            return None, len(body) + len(chunk)
        body.extend(chunk)
    raw_body = bytes(body)
    request._body = raw_body  # pyright: ignore[reportPrivateUsage]  # 单次有界读取后供同一 Request 复用。
    return raw_body, len(raw_body)


def _permits_wms_event_endpoint(policy: object) -> bool:
    return isinstance(policy, WmsInboundAuthPolicy) and policy.allows_unsigned_wms_callbacks


def _unavailable_ack(raw_body: bytes) -> JSONResponse | Response:
    try:
        decoded = raw_body.decode("utf-8")
    except UnicodeDecodeError:
        return Response(status_code=400)
    try:
        raw_envelope = loads_transport_json(decoded)
    except StrictJsonError as error:
        if error.duplicate_key:
            return Response(status_code=400)
        operation_id = error.operation_id
        if not is_wire_operation_id(operation_id) or not is_wire_operation(error.operation):
            return Response(status_code=400)
        return _unavailable_response(operation_id)
    if not isinstance(raw_envelope, dict):
        return Response(status_code=400)
    envelope = cast("dict[str, Any]", raw_envelope)
    operation_id = envelope.get("operation_id")
    if not is_wire_operation_id(operation_id) or not is_wire_operation(envelope.get("operation")):
        return Response(status_code=400)
    return _unavailable_response(operation_id)


def _unavailable_response(operation_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "operation_id": operation_id,
            "code": "UNAVAILABLE",
            "timestamp": int(timezone.now_utc().timestamp() * 1000),
            "data": {},
        },
    )


def _unsupported_operation_ack(raw_body: bytes) -> JSONResponse | Response:
    try:
        value = loads_transport_json(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, StrictJsonError):
        return Response(status_code=400)
    if not isinstance(value, dict):
        return Response(status_code=400)
    operation_id = value.get("operation_id")
    if not is_wire_operation_id(operation_id) or not is_wire_operation(value.get("operation")):
        return Response(status_code=400)
    return JSONResponse(
        status_code=422,
        content={
            "operation_id": operation_id,
            "code": "REJECTED",
            "timestamp": int(timezone.now_utc().timestamp() * 1000),
            "data": {"reason_code": "UNSUPPORTED_OPERATION"},
        },
    )


def _valid_wms_event_request_headers(request: Request) -> bool:
    content_types = request.headers.getlist("content-type")
    if len(content_types) != 1 or not is_json_utf8_media_type(content_types[0]):
        return False
    content_encodings = request.headers.getlist("content-encoding")
    return len(content_encodings) <= 1 and (
        not content_encodings or content_encodings[0].strip().casefold() == "identity"
    )


def _enqueue_transport_evidence() -> None:
    try:
        task_queue_gateway.enqueue_transport_evidence()
    except Exception:
        logger.warning(
            "transport.evidence.enqueue_failed",
            extra={"event": "transport.evidence.enqueue_failed"},
            exc_info=True,
        )


def _transport_identity(raw_body: bytes) -> dict[str, Any]:
    try:
        decoded = loads_transport_json(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, StrictJsonError):
        return {}
    if not isinstance(decoded, dict):
        return {}
    envelope = cast("dict[str, Any]", decoded)
    data = envelope.get("data")
    data = data if isinstance(data, dict) else {}
    return {
        "operation_id": _safe_diagnostic_text(envelope.get("operation_id"), max_length=36),
        "operation": _safe_diagnostic_text(envelope.get("operation"), max_length=80),
        "transport_task_id": _safe_diagnostic_text(data.get("transport_task_id"), max_length=80),
        "kind": data.get("kind")
        if data.get("kind") in {"RACK_MOVE", "RACK_ROTATE", "BIN_MOVE", "BIN_EXCHANGE"}
        else None,
        "outcome_revision": (
            data.get("outcome_revision")
            if isinstance(data.get("outcome_revision"), int)
            and not isinstance(data.get("outcome_revision"), bool)
            and data.get("outcome_revision") > 0
            else None
        ),
    }


def _safe_diagnostic_text(value: object, *, max_length: int) -> str | None:
    if not isinstance(value, str) or len(value) > max_length:
        return None
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return None
    return value


async def _publish_transport_ingress_attempt(
    request: Request,
    *,
    request_id: str,
    received_at: str,
    raw_body: bytes,
    observed_body_bytes: int,
    status_code: int,
    disposition: TransportIngressDisposition,
    error_code: str | None,
    include_identity: bool = True,
) -> None:
    identity = _transport_identity(raw_body) if include_identity else {}
    event = TransportIngressAttempt(
        request_id=request_id,
        operation_id=identity.get("operation_id"),
        operation=identity.get("operation"),
        transport_task_id=identity.get("transport_task_id"),
        kind=identity.get("kind"),
        outcome_revision=identity.get("outcome_revision"),
        received_at=received_at,
        disposition=disposition,
        status_code=status_code,
        error_code=error_code,
        observed_body_bytes=observed_body_bytes,
    )
    publisher = getattr(request.app.state, "transport_event_stream_service", event_stream_service)
    try:
        await publisher.publish_to(
            TRANSPORT_EVIDENCE_STREAM_CHANNEL,
            "transport_ingress.attempted",
            event.model_dump(mode="json"),
        )
    except Exception:
        logger.warning(
            "transport.ingress.event_publish_failed",
            extra={"event": "transport.ingress.event_publish_failed"},
            exc_info=True,
        )


def _disposition(code: object, status_code: int) -> TransportIngressDisposition:
    try:
        return TransportIngressDisposition(code)
    except (TypeError, ValueError):
        if status_code == 409:
            return TransportIngressDisposition.CONFLICT
        if status_code == 503:
            return TransportIngressDisposition.UNAVAILABLE
        return TransportIngressDisposition.REJECTED


@router.post(
    "/events",
    responses=WMS_EVENT_RESPONSES,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": WMS_EVENT_REQUEST_SCHEMA}},
        }
    },
)
async def receive_wms_event(request: Request) -> Response:
    request_id = new_uuid7()
    received_at = timezone.now_utc().isoformat()
    if not _valid_wms_event_request_headers(request):
        await _publish_transport_ingress_attempt(
            request,
            request_id=request_id,
            received_at=received_at,
            raw_body=b"",
            observed_body_bytes=0,
            status_code=400,
            disposition=TransportIngressDisposition.REJECTED,
            error_code="INVALID_CONTENT_TYPE",
            include_identity=False,
        )
        return Response(status_code=400)
    raw_body, observed_body_bytes = await _read_bounded_body(request)
    if raw_body is None:
        await _publish_transport_ingress_attempt(
            request,
            request_id=request_id,
            received_at=received_at,
            raw_body=b"",
            observed_body_bytes=observed_body_bytes,
            status_code=413,
            disposition=TransportIngressDisposition.REJECTED,
            error_code="BODY_TOO_LARGE",
            include_identity=False,
        )
        return Response(status_code=413)

    policy = getattr(request.app.state, "wms_inbound_auth_policy", None)
    if not _permits_wms_event_endpoint(policy):
        await _publish_transport_ingress_attempt(
            request,
            request_id=request_id,
            received_at=received_at,
            raw_body=raw_body,
            observed_body_bytes=observed_body_bytes,
            status_code=401,
            disposition=TransportIngressDisposition.REJECTED,
            error_code="UNAUTHORIZED",
            include_identity=False,
        )
        return Response(status_code=401)

    operation = _extract_operation(raw_body)
    is_inbound_event = operation == RECOVERY_OPERATION
    if is_inbound_event:
        handler = getattr(request.app.state, "wms_recovery_event_handler", None)
        if handler is None:
            return _unavailable_ack(raw_body)
        result = await handler.handle(raw_body)
    elif operation in {TRANSPORT_POSITION_OPERATION, TRANSPORT_RESULT_OPERATION}:
        runtime: TransportRuntime | None = getattr(request.app.state, "transport_runtime", None)
        if runtime is None:
            response = _unavailable_ack(raw_body)
            await _publish_transport_ingress_attempt(
                request,
                request_id=request_id,
                received_at=received_at,
                raw_body=raw_body,
                observed_body_bytes=observed_body_bytes,
                status_code=response.status_code,
                disposition=(
                    TransportIngressDisposition.UNAVAILABLE
                    if response.status_code == 503
                    else TransportIngressDisposition.REJECTED
                ),
                error_code="TRANSPORT_RUNTIME_UNAVAILABLE" if response.status_code == 503 else "INVALID_ENVELOPE",
            )
            return response
        result = await runtime.handler.handle(raw_body)
    else:
        return _unsupported_operation_ack(raw_body)
    # Evidence 已持久化后先应答 WMS；Celery 唤醒只是加速提示，失败时由 Beat 兜底扫描。
    background = (
        (BackgroundTask(_enqueue_transport_evidence) if result.body.get("code") in {"RECEIVED", "DUPLICATE"} else None)
        if not is_inbound_event
        else None
    )
    if result.body:
        response: Response = JSONResponse(status_code=result.http_status, content=result.body, background=background)
    else:
        response = Response(status_code=result.http_status, background=background)
    if not is_inbound_event:
        code = result.body.get("code")
        await _publish_transport_ingress_attempt(
            request,
            request_id=request_id,
            received_at=received_at,
            raw_body=raw_body,
            observed_body_bytes=observed_body_bytes,
            status_code=result.http_status,
            disposition=_disposition(code, result.http_status),
            error_code=(
                None
                if code in {"RECEIVED", "DUPLICATE"}
                else code
                if isinstance(code, str)
                else f"HTTP_{result.http_status}"
            ),
        )
    return response


def _extract_operation(raw_body: bytes) -> str | None:
    try:
        value = loads_transport_json(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, StrictJsonError):
        return None
    if not isinstance(value, dict):
        return None
    envelope = cast("dict[str, Any]", value)
    operation = envelope.get("operation")
    return operation if isinstance(operation, str) else None


__all__ = ["router"]
