"""WMS Transport evidence 唯一生产入口。"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from src.app.transport.contracts import TransportContractError
from src.app.wms_adapter.inbound_auth import WmsInboundAuthPolicy
from src.app.wms_adapter.transport_event_handler import MAX_TRANSPORT_EVENT_BODY_BYTES
from src.app.wms_adapter.transport_wire import POSITION_OPERATION, RESULT_OPERATION, validate_callback_envelope
from src.core.task_queue_gateway import task_queue_gateway
from src.core.uuid7 import is_uuid7
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from src.app.transport.composition import TransportRuntime

logger = logging.getLogger(__name__)
router = APIRouter()


def _closed_object(required: list[str], properties: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


_UUIDV7_SCHEMA = {
    "type": "string",
    "pattern": "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$",
    "description": "WMS 生成的 UUIDv7 幂等号",
}
_TIMESTAMP_SCHEMA = {"type": "integer", "format": "int64", "description": "Unix 毫秒时间戳"}
_TRANSPORT_TASK_ID_SCHEMA = {"type": "string", "minLength": 1, "maxLength": 80}
_OBJECT_ID_SCHEMA = {"type": "string", "minLength": 1, "maxLength": 100}
_RACK_POSITION_SCHEMA = _closed_object(
    ["kind", "location_code"],
    {
        "kind": {"type": "string", "const": "RACK_POSITION"},
        "location_code": {"type": "string", "minLength": 1},
    },
)
_BIN_POSITION_SCHEMA = {
    "oneOf": [
        _closed_object(
            ["kind", "rack_id", "slot_id"],
            {
                "kind": {"type": "string", "const": "RACK_BIN_SLOT"},
                "rack_id": {"type": "string", "minLength": 1},
                "slot_id": {"type": "string", "minLength": 1},
            },
        ),
        _closed_object(
            ["kind", "location_code"],
            {
                "kind": {"type": "string", "const": "HANDOFF_POSITION"},
                "location_code": {"type": "string", "minLength": 1},
            },
        ),
    ]
}
_ANY_POSITION_SCHEMA = {"oneOf": [_RACK_POSITION_SCHEMA, *_BIN_POSITION_SCHEMA["oneOf"]]}

_POSITION_DATA_SCHEMA = {
    "oneOf": [
        _closed_object(
            ["transport_task_id", "bin_id", "milestone"],
            {
                "transport_task_id": _TRANSPORT_TASK_ID_SCHEMA,
                "bin_id": _OBJECT_ID_SCHEMA,
                "milestone": {"type": "string", "const": milestone},
            },
        )
        for milestone in ("SOURCE_PICKED", "POSITION_UNKNOWN")
    ]
    + [
        _closed_object(
            ["transport_task_id", "bin_id", "milestone", "final_position"],
            {
                "transport_task_id": _TRANSPORT_TASK_ID_SCHEMA,
                "bin_id": _OBJECT_ID_SCHEMA,
                "milestone": {"type": "string", "const": "TARGET_PLACED"},
                "final_position": _ANY_POSITION_SCHEMA,
            },
        )
    ]
}


def _member_result_schema(*, final_position: dict[str, object], arrival_face: bool) -> dict[str, object]:
    success_properties: dict[str, object] = {
        "object_id": _OBJECT_ID_SCHEMA,
        "status": {"type": "string", "const": "SUCCEEDED"},
        "final_position": final_position,
    }
    failed_properties: dict[str, object] = {
        "object_id": _OBJECT_ID_SCHEMA,
        "status": {"type": "string", "const": "FAILED"},
        "final_position": final_position,
        "failure_code": {"type": "string", "minLength": 1, "maxLength": 120},
    }
    success_required = ["object_id", "status", "final_position"]
    failed_required = ["object_id", "status", "final_position", "failure_code"]
    if arrival_face:
        arrival_schema = {"type": "string", "enum": ["A", "B"]}
        success_properties["arrival_face"] = arrival_schema
        failed_properties["arrival_face"] = arrival_schema
        success_required.append("arrival_face")
        failed_required.append("arrival_face")
    return {
        "oneOf": [
            _closed_object(success_required, success_properties),
            _closed_object(failed_required, failed_properties),
            _closed_object(
                ["object_id", "status", "position_unknown", "failure_code"],
                {
                    "object_id": _OBJECT_ID_SCHEMA,
                    "status": {"type": "string", "const": "FAILED"},
                    "position_unknown": {"type": "boolean", "const": True},
                    "failure_code": {"type": "string", "minLength": 1, "maxLength": 120},
                },
            ),
        ]
    }


_RESULT_DATA_SCHEMA = {
    "oneOf": [
        _closed_object(
            ["transport_task_id", "kind", "results"],
            {
                "transport_task_id": _TRANSPORT_TASK_ID_SCHEMA,
                "kind": {"type": "string", "enum": ["RACK_MOVE", "RACK_ROTATE"]},
                "results": {
                    "type": "array",
                    "minItems": 1,
                    "items": _member_result_schema(final_position=_RACK_POSITION_SCHEMA, arrival_face=True),
                },
            },
        ),
        _closed_object(
            ["transport_task_id", "kind", "results"],
            {
                "transport_task_id": _TRANSPORT_TASK_ID_SCHEMA,
                "kind": {"type": "string", "enum": ["BIN_MOVE", "BIN_EXCHANGE"]},
                "results": {
                    "type": "array",
                    "minItems": 1,
                    "items": _member_result_schema(final_position=_BIN_POSITION_SCHEMA, arrival_face=False),
                },
            },
        ),
    ]
}


def _event_envelope_schema(operation: str, data_schema: dict[str, object]) -> dict[str, object]:
    return _closed_object(
        ["operation_id", "operation", "timestamp", "data"],
        {
            "operation_id": _UUIDV7_SCHEMA,
            "operation": {"type": "string", "const": operation},
            "timestamp": _TIMESTAMP_SCHEMA,
            "data": data_schema,
        },
    )


_TRANSPORT_EVENT_REQUEST_SCHEMA = {
    "oneOf": [
        _event_envelope_schema(POSITION_OPERATION, _POSITION_DATA_SCHEMA),
        _event_envelope_schema(RESULT_OPERATION, _RESULT_DATA_SCHEMA),
    ]
}


def _ack_schema(code: str, data_schema: dict[str, object]) -> dict[str, object]:
    return _closed_object(
        ["operation_id", "code", "timestamp", "data"],
        {
            "operation_id": _UUIDV7_SCHEMA,
            "code": {"type": "string", "const": code},
            "timestamp": _TIMESTAMP_SCHEMA,
            "data": data_schema,
        },
    )


_ACK_TASK_DATA_SCHEMA = _closed_object(["transport_task_id"], {"transport_task_id": _TRANSPORT_TASK_ID_SCHEMA})
_TRANSPORT_EVENT_ACK_SCHEMA = {
    "oneOf": [
        *(_ack_schema(code, _ACK_TASK_DATA_SCHEMA) for code in ("RECEIVED", "DUPLICATE", "CONFLICT")),
        _ack_schema(
            "REJECTED",
            _closed_object(
                ["reason_code"],
                {"reason_code": {"type": "string", "minLength": 1, "maxLength": 120}},
            ),
        ),
        _ack_schema("UNAVAILABLE", _closed_object([], {})),
    ]
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


def _unavailable_ack(raw_body: bytes) -> JSONResponse | Response:
    try:
        raw_envelope = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return Response(status_code=400)
    operation_id = raw_envelope.get("operation_id") if isinstance(raw_envelope, dict) else None
    if not is_uuid7(operation_id):
        return Response(status_code=400)
    try:
        validate_callback_envelope(raw_envelope)
    except TransportContractError:
        return JSONResponse(
            status_code=422,
            content={
                "operation_id": operation_id,
                "code": "REJECTED",
                "timestamp": int(timezone.now_utc().timestamp() * 1000),
                "data": {"reason_code": "INVALID_EVIDENCE"},
            },
        )
    return JSONResponse(
        status_code=503,
        content={
            "operation_id": operation_id,
            "code": "UNAVAILABLE",
            "timestamp": int(timezone.now_utc().timestamp() * 1000),
            "data": {},
        },
    )


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
        422: {
            "description": "evidence data 不满足对应 operation 的封闭合同",
            "content": {"application/json": {"schema": _TRANSPORT_EVENT_ACK_SCHEMA}},
        },
        503: {
            "description": "Transport runtime 尚未就绪或当前无法可靠持久化",
            "content": {"application/json": {"schema": _TRANSPORT_EVENT_ACK_SCHEMA}},
        },
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
