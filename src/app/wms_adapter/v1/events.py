"""WMS Transport evidence 唯一生产入口。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from src.app.wms_adapter.inbound_auth import WmsInboundAuthPolicy
from src.app.wms_adapter.transport_event_handler import MAX_TRANSPORT_EVENT_BODY_BYTES
from src.app.wms_adapter.transport_wire import POSITION_OPERATION, RESULT_OPERATION
from src.core.task_queue_gateway import task_queue_gateway

if TYPE_CHECKING:
    from src.app.transport.composition import TransportRuntime

logger = logging.getLogger(__name__)
router = APIRouter()

_TRANSPORT_EVENT_REQUEST_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["operation_id", "operation", "timestamp", "data"],
    "properties": {
        "operation_id": {"type": "string", "description": "WMS 生成的 UUIDv7 幂等号"},
        "operation": {"type": "string", "enum": [POSITION_OPERATION, RESULT_OPERATION]},
        "timestamp": {"type": "integer", "format": "int64", "description": "Unix 毫秒时间戳"},
        "data": {"type": "object", "description": "由 operation 决定的封闭 evidence data 合同"},
    },
}
_TRANSPORT_EVENT_ACK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["operation_id", "code", "timestamp", "data"],
    "properties": {
        "operation_id": {"type": "string"},
        "code": {"type": "string", "enum": ["RECEIVED", "DUPLICATE", "CONFLICT"]},
        "timestamp": {"type": "integer", "format": "int64"},
        "data": {"type": "object"},
    },
}


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


@router.post(
    "/events",
    responses={
        200: {
            "description": "重复 evidence 已确认",
            "content": {"application/json": {"schema": _TRANSPORT_EVENT_ACK_SCHEMA}},
        },
        202: {
            "description": "evidence 已持久化",
            "content": {"application/json": {"schema": _TRANSPORT_EVENT_ACK_SCHEMA}},
        },
        400: {"description": "evidence envelope 不满足封闭合同"},
        401: {"description": "当前冻结 profile 不允许无签名 WMS Transport callback"},
        409: {
            "description": "operation_id 对应的 payload 冲突",
            "content": {"application/json": {"schema": _TRANSPORT_EVENT_ACK_SCHEMA}},
        },
        413: {"description": "请求体超过固定上限"},
        503: {"description": "Transport runtime 尚未就绪"},
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": _TRANSPORT_EVENT_REQUEST_SCHEMA}},
        }
    },
)
async def receive_transport_event(request: Request) -> Response:
    raw_body = await _read_bounded_body(request)
    if raw_body is None:
        return Response(status_code=413)

    policy = getattr(request.app.state, "wms_inbound_auth_policy", None)
    if not _permits_transport_endpoint(policy):
        return Response(status_code=401)

    runtime: TransportRuntime | None = getattr(request.app.state, "transport_runtime", None)
    if runtime is None:
        return Response(status_code=503)
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
