"""
WMS Mock 服务

模拟上位 WMS 系统，提供主数据查询和库存操作接口。

运行方式：
    python tests/mock/wms_mock_server.py
    或
    uv run python tests/mock/wms_mock_server.py
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import math
import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Literal
from uuid import uuid4

import httpx
from fastapi import BackgroundTasks, FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import ClientDisconnect
from uvicorn import Config, Server

from src.app.wms_integration.operation_contract import WmsCompletionMode
from src.app.wms_integration.operation_registry import WMS_OPERATION_BY_IDENTITY, WMS_OPERATIONS

# 添加项目根目录到 sys.path
project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# 北向 contract 核心必须同时支持 pytest package import 与 Docker 的脚本入口。
from tests.mock.wms_northbound_contract import (
    ACTIVE_MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE,
    NorthboundAuthError,
    NorthboundHmacReplayGuard,
    NorthboundOperationStore,
    NorthboundPayloadValidationError,
    build_typed_ack,
    build_typed_result,
    canonical_payload_bytes,
    content_sha256,
    validate_typed_request,
    verify_status_hmac,
    verify_submit_hmac,
)

_ASYNC_SUBMIT_DEADLINES = frozenset(
    operation.budget.deadline_seconds
    for operation in WMS_OPERATION_BY_IDENTITY.values()
    if operation.completion_mode is WmsCompletionMode.ASYNC_TASK
)
if len(_ASYNC_SUBMIT_DEADLINES) != 1:
    raise RuntimeError("frozen ASYNC_TASK operations must share one submit deadline")
_DEFAULT_ASYNC_SUBMIT_DEADLINE = str(next(iter(_ASYNC_SUBMIT_DEADLINES)))


logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

CALLBACK_API_APP_ID = os.getenv("API_APP_ID", "")
CALLBACK_API_APP_SECRET = os.getenv("API_APP_SECRET", "")
WES_EXTERNAL_CALLBACK_URL = os.getenv(
    "WES_EXTERNAL_CALLBACK_URL",
    "http://localhost:8001/api/v1/callback/external",
)

# 北向 contract 的时钟、故障与 callback hint 状态必须随 Mock reset 一并清理。
northbound_clock_state: dict[str, datetime | None] = {"now": None}


@dataclass(frozen=True, slots=True)
class _NorthboundFault:
    status: int
    target_path: str
    method: str
    operation_identity: str | None
    retry_after: int | None
    delay: float
    after_response: bool
    not_found: bool
    max_response_bytes: int | None
    response_body_bytes: int | None


northbound_fault_lock = RLock()
northbound_fault_state: dict[str, _NorthboundFault | None] = {"next": None}
northbound_callback_hint_evidence: dict[tuple[str, str], dict[str, str]] = {}


def _northbound_now() -> datetime:
    return northbound_clock_state["now"] or datetime.now(UTC)


def _positive_finite_env_float(name: str, default: str) -> float:
    value = float(os.getenv(name, default))
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return value


northbound_operation_store = NorthboundOperationStore(
    clock=_northbound_now,
    retention_seconds=int(os.getenv("WMS_EFFECT_IDEMPOTENCY_RETENTION_SECONDS", "9")),
    visibility_sla_seconds=_positive_finite_env_float("WMS_EFFECT_STATUS_VISIBILITY_SLA_SECONDS", "2"),
)
northbound_hmac_replay_guard = NorthboundHmacReplayGuard()


def reset_mock_wms_state() -> None:
    northbound_operation_store.reset()
    northbound_hmac_replay_guard.reset()
    northbound_clock_state["now"] = None
    with northbound_fault_lock:
        northbound_fault_state["next"] = None
    northbound_callback_hint_evidence.clear()


# ============================================
# FastAPI 应用
# ============================================

app = FastAPI(title="WMS Mock Server", description="模拟 WMS typed operation 合同", version="1.0.0")


def _northbound_fault_matches(fault: _NorthboundFault, request: Request) -> bool:
    if request.method != fault.method or request.url.path != fault.target_path:
        return False
    if fault.operation_identity is None:
        return True
    actual_identity = request.headers.get("X-WES-Operation-Identity") or request.query_params.get("operation_identity")
    return actual_identity == fault.operation_identity


def _northbound_not_found_payload() -> dict[str, Any]:
    return {
        "state": "NOT_FOUND",
        "provider_reference": None,
        "accepted_scope": None,
        "reason_code": None,
        "updated_at": None,
        "source_version": None,
        "result_payload": None,
    }


def _northbound_fault_response(fault: _NorthboundFault) -> Response:
    if 500 <= fault.status <= 599:
        code = "TEMPORARILY_UNAVAILABLE"
    elif fault.status == 429:
        code = "RATE_LIMITED"
    else:
        code = "NORTHBOUND_FAULT"
    prefix = json.dumps({"code": code}, separators=(",", ":")).encode()
    headers = {"Retry-After": str(fault.retry_after)} if fault.status == 429 and fault.retry_after is not None else None
    if fault.response_body_bytes is None:
        content = prefix if fault.max_response_bytes is None else prefix[: fault.max_response_bytes]
        return Response(content=content, status_code=fault.status, media_type="application/json", headers=headers)

    async def stream_body():
        remaining_budget = fault.max_response_bytes
        prefix_chunk = prefix if remaining_budget is None else prefix[:remaining_budget]
        if prefix_chunk:
            yield prefix_chunk
        if remaining_budget is not None:
            remaining_budget = max(remaining_budget - len(prefix_chunk), 0)
        remaining_filler = fault.response_body_bytes
        if remaining_budget is not None:
            remaining_filler = min(remaining_filler, remaining_budget)
        while remaining_filler > 0:
            chunk_size = min(remaining_filler, 1024)
            yield b"x" * chunk_size
            remaining_filler -= chunk_size

    return StreamingResponse(stream_body(), status_code=fault.status, media_type="application/json", headers=headers)


def _status_operation_identity_error(request: Request) -> JSONResponse | None:
    """在任何测试 fault 前拒绝不属于 E08–E14 的 status identity。"""

    if request.url.path != "/northbound/operations/status":
        return None
    operation_identity = request.query_params.get("operation_identity", "").strip()
    operation = WMS_OPERATION_BY_IDENTITY.get(operation_identity)
    if operation is None:
        return JSONResponse(status_code=422, content={"code": "STATUS_OPERATION_UNKNOWN"})
    if operation.completion_mode is not WmsCompletionMode.ASYNC_TASK:
        return JSONResponse(status_code=422, content={"code": "STATUS_OPERATION_NOT_ASYNC_EFFECT"})
    return None


@app.middleware("http")
async def fault_injection_middleware(request: Request, call_next):
    if request.url.path.startswith("/debug"):
        return await call_next(request)

    if status_error := _status_operation_identity_error(request):
        return status_error

    # 一次性 fault 必须在首个 await 前由同步锁原子认领，避免并发请求重复消费。
    claimed_fault: _NorthboundFault | None = None
    with northbound_fault_lock:
        configured_fault = northbound_fault_state["next"]
        if configured_fault is not None and _northbound_fault_matches(configured_fault, request):
            claimed_fault = configured_fault
            northbound_fault_state["next"] = None

    if claimed_fault is not None and claimed_fault.after_response:
        response = await call_next(request)
        if claimed_fault.delay > 0:
            await asyncio.sleep(claimed_fault.delay)
        return response

    if claimed_fault is not None:
        if claimed_fault.delay > 0:
            await asyncio.sleep(claimed_fault.delay)
        if claimed_fault.not_found:
            return JSONResponse(status_code=200, content=_northbound_not_found_payload())
        if claimed_fault.status != 200 or claimed_fault.response_body_bytes is not None:
            return _northbound_fault_response(claimed_fault)

    return await call_next(request)


# --- Debug/Mock 接口 ---


class NorthboundFaultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: int = 500
    target_path: str
    method: str
    operation_identity: str | None = None
    retry_after: int | None = None
    delay: float = Field(default=0.0, ge=0)
    after_response: bool = False
    not_found: bool = False
    max_response_bytes: int | None = Field(default=None, ge=1, le=16 * 1024 * 1024)
    response_body_bytes: int | None = Field(default=None, ge=0, le=16 * 1024 * 1024)


class NorthboundRejectRequest(BaseModel):
    operation_identity: str
    idempotency_key: str
    reason_code: str


class NorthboundClockRequest(BaseModel):
    now: str | None = None


class NorthboundVisibilityRequest(BaseModel):
    operation_identity: str
    idempotency_key: str
    delay_seconds: float = Field(ge=0, allow_inf_nan=False)


@app.post("/debug/reset", summary="恢复 WMS Mock 初始状态")
async def debug_reset():
    reset_mock_wms_state()
    return {"code": 200, "data": {"reset": True}}


@app.post("/debug/northbound/faults", summary="注入北向 HTTP 故障")
async def debug_set_northbound_fault(request: NorthboundFaultRequest):
    """仅供 Mock 验收模拟限流、服务端错误、慢响应与响应体边界。"""

    method = request.method.strip().upper()
    target_path = request.target_path.strip()
    if method not in {"GET", "POST"} or not target_path.startswith(
        ("/northbound/", "/api/wms/inventory/confirm-inbound", "/api/wms/fulfillment/")
    ):
        return JSONResponse(status_code=422, content={"code": "INVALID_NORTHBOUND_FAULT_SCOPE"})
    configured = _NorthboundFault(
        status=request.status,
        target_path=target_path,
        method=method,
        operation_identity=request.operation_identity,
        retry_after=request.retry_after,
        delay=request.delay,
        after_response=request.after_response,
        not_found=request.not_found,
        max_response_bytes=request.max_response_bytes,
        response_body_bytes=request.response_body_bytes,
    )
    with northbound_fault_lock:
        northbound_fault_state["next"] = configured
    return {
        "code": 200,
        "data": {
            "status": configured.status,
            "target_path": configured.target_path,
            "method": configured.method,
            "operation_identity": configured.operation_identity,
            "retry_after": configured.retry_after,
            "delay": configured.delay,
            "after_response": configured.after_response,
            "not_found": configured.not_found,
            "max_response_bytes": configured.max_response_bytes,
            "response_body_bytes": configured.response_body_bytes,
        },
    }


@app.post("/debug/northbound/reject", summary="将北向请求置为业务拒绝")
async def debug_reject_northbound_operation(request: NorthboundRejectRequest):
    snapshot = northbound_operation_store.reject(
        request.operation_identity,
        request.idempotency_key,
        reason_code=request.reason_code,
    )
    return snapshot.as_dict()


@app.post("/debug/northbound/clock", summary="设置北向 Mock 时钟")
async def debug_set_northbound_clock(request: NorthboundClockRequest):
    if request.now is None:
        northbound_clock_state["now"] = None
    else:
        parsed = datetime.fromisoformat(request.now)
        if parsed.tzinfo is None:
            return JSONResponse(status_code=422, content={"code": "CLOCK_MUST_BE_TIMEZONE_AWARE"})
        northbound_clock_state["now"] = parsed.astimezone(UTC)
    return {
        "code": 200,
        "data": {"now": northbound_clock_state["now"].isoformat() if northbound_clock_state["now"] else None},
    }


@app.post("/debug/northbound/visibility", summary="设置北向状态暂时不可见次数")
async def debug_set_northbound_visibility(request: NorthboundVisibilityRequest):
    """仅供黑盒探针按公开时钟模拟受理后在 SLA 内暂时返回 NOT_FOUND。"""

    try:
        northbound_operation_store.configure_visibility_delay(
            request.operation_identity,
            request.idempotency_key,
            delay_seconds=request.delay_seconds,
        )
    except ValueError:
        return JSONResponse(status_code=422, content={"code": "VISIBILITY_DELAY_OUTSIDE_SLA"})
    return {
        "operation_identity": request.operation_identity,
        "idempotency_key": request.idempotency_key,
        "delay_seconds": request.delay_seconds,
    }


@app.get("/debug/northbound/effects", summary="查询北向业务效果计数")
async def debug_northbound_effect_count(operation_identity: str, idempotency_key: str):
    return {
        "operation_identity": operation_identity,
        "idempotency_key": idempotency_key,
        "effect_count": northbound_operation_store.effect_count(operation_identity, idempotency_key),
    }


@app.get("/debug/northbound/callback-hints", summary="查询北向 callback hint 脱敏投影")
async def debug_northbound_callback_hints(operation_identity: str, idempotency_key: str):
    hint = northbound_callback_hint_evidence.get((operation_identity, idempotency_key))
    return {"hints": [] if hint is None else [hint]}


async def _request_body_or_none(request: Request) -> bytes | None:
    """客户端已因 deadline 断开时安静终止，不把预期超时记录成 ASGI 异常。"""

    try:
        return await request.body()
    except ClientDisconnect:
        return None


@app.get("/northbound/contract", summary="查询北向 Mock 合同承诺")
async def northbound_contract():
    return {
        "credential_reference": ACTIVE_MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE,
        "idempotency_retention_seconds": int(os.getenv("WMS_EFFECT_IDEMPOTENCY_RETENTION_SECONDS", "9")),
        "status_visibility_sla_seconds": _positive_finite_env_float("WMS_EFFECT_STATUS_VISIBILITY_SLA_SECONDS", "2"),
        "max_response_bytes": int(os.getenv("WMS_EFFECT_STATUS_MAX_RESPONSE_BYTES", "4096")),
        "submit_deadline_seconds": _positive_finite_env_float(
            "WMS_EFFECT_SUBMIT_TIMEOUT_SECONDS",
            _DEFAULT_ASYNC_SUBMIT_DEADLINE,
        ),
        "status_deadline_seconds": _positive_finite_env_float("WMS_EFFECT_STATUS_TIMEOUT_SECONDS", "2"),
    }


@app.get("/northbound/operations/status", summary="查询北向 typed EFFECT 权威状态")
async def northbound_operation_status(request: Request, operation_identity: str, idempotency_key: str):
    if status_error := _status_operation_identity_error(request):
        return status_error
    raw_path = request.scope["path"]
    query_string = request.scope.get("query_string", b"")
    if query_string:
        raw_path = f"{raw_path}?{query_string.decode('ascii')}"
    body = await _request_body_or_none(request)
    if body is None:
        return Response(status_code=499)
    try:
        verify_status_hmac(request.headers, body, method=request.method, path=raw_path)
        northbound_hmac_replay_guard.consume(
            credential_reference=request.headers["X-WMS-Credential-Reference"],
            timestamp=request.headers["X-WMS-Timestamp"],
            nonce=request.headers["X-WMS-Nonce"],
        )
        snapshot = northbound_operation_store.query(operation_identity, idempotency_key)
    except NorthboundAuthError as exc:
        return JSONResponse(status_code=401, content={"code": exc.code})
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"code": str(exc)})
    return snapshot.as_dict()


async def _post_callback(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        if not CALLBACK_API_APP_ID or not CALLBACK_API_APP_SECRET:
            raise RuntimeError("WMS Mock callback API credential is required")
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        timestamp = str(int(time.time()))
        nonce = uuid4().hex
        body_sha256 = hashlib.sha256(body).hexdigest()
        path = httpx.URL(url).path
        canonical = f"POST\n{path}\n{timestamp}\n{nonce}\n{body_sha256}\n{CALLBACK_API_APP_ID}"
        signature = hmac.new(
            CALLBACK_API_APP_SECRET.encode(),
            canonical.encode(),
            hashlib.sha256,
        ).hexdigest()
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-App-ID": CALLBACK_API_APP_ID,
                    "X-Timestamp": timestamp,
                    "X-Nonce": nonce,
                    "X-Body-SHA256": body_sha256,
                    "X-Signature": signature,
                },
            )
        return {
            "delivered": 200 <= response.status_code < 300,
            "status_code": response.status_code,
            "response_text": response.text,
        }
    except Exception as exc:
        logger.error("WMS Mock 回调 WES 失败: %s", exc)
        return {"delivered": False, "error": str(exc)}


def _typed_effect_callback_payload(
    *,
    operation_identity: str,
    idempotency_key: str,
    request_payload: dict[str, Any],
) -> dict[str, Any]:
    dispatch_key = str(request_payload.get("dispatch_key") or "")
    return {
        "callback_type": "WMS_EFFECT_STATUS_HINT",
        "source_system": "WMS",
        "source_event_id": f"wms-mock:typed-effect:{uuid4().hex}",
        "occurred_at": datetime.now(UTC).isoformat(),
        "trace_id": str(request_payload.get("trace_id") or f"wms-mock:{dispatch_key}"),
        "data": {
            "operation_identity": operation_identity,
            "idempotency_key": idempotency_key,
            "dispatch_key": dispatch_key,
        },
    }


async def _submit_northbound_effect(
    *,
    request: Request,
    background_tasks: BackgroundTasks,
    operation_identity: str,
) -> Response:
    """以共享状态核心受理 typed EFFECT，同时保留各 operation 的既有响应字段。"""

    body = await _request_body_or_none(request)
    if body is None:
        return Response(status_code=499)
    try:
        verify_submit_hmac(request.headers, body, method=request.method, path=request.url.path)
        northbound_hmac_replay_guard.consume(
            credential_reference=request.headers["X-WES-Credential-Reference"],
            timestamp=request.headers["X-WES-Timestamp"],
            nonce=request.headers["X-WES-Nonce"],
        )
        submitted_identity = str(request.headers.get("X-WES-Operation-Identity") or "")
        if submitted_identity != operation_identity:
            return JSONResponse(status_code=422, content={"code": "OPERATION_IDENTITY_MISMATCH"})
        if request.headers.get("content-type", "").partition(";")[0].strip().lower() != "application/json":
            raise NorthboundPayloadValidationError("typed request content type must be application/json")
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NorthboundPayloadValidationError("typed request must contain valid JSON") from exc
        if not isinstance(payload, dict):
            raise NorthboundPayloadValidationError("typed request must be a JSON object")
        idempotency_key = str(request.headers.get("Idempotency-Key") or "")
        validated_payload = validate_typed_request(operation_identity, payload)
        submission = northbound_operation_store.submit(
            operation_identity,
            idempotency_key,
            content_sha256(canonical_payload_bytes(validated_payload)),
            validated_payload,
        )
    except NorthboundAuthError as exc:
        return JSONResponse(status_code=401, content={"code": exc.code})
    except NorthboundPayloadValidationError:
        return JSONResponse(status_code=422, content={"code": "INVALID_TYPED_REQUEST"})
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"code": str(exc)})

    if submission.error_code is not None:
        replay_data = None
        if submission.error_code == "IDEMPOTENCY_REQUEST_IN_PROGRESS":
            replay_data = _northbound_response_data(
                operation_identity,
                idempotency_key,
                validated_payload,
                submission_state="IN_PROGRESS_REPLAY",
            )
        return JSONResponse(
            status_code=submission.status_code,
            content={
                "code": submission.error_code,
                "data": replay_data,
            },
        )

    operation = WMS_OPERATION_BY_IDENTITY[operation_identity]
    if operation.completion_mode is WmsCompletionMode.SYNC_RESULT:
        if submission.snapshot is None or submission.snapshot.result_payload is None:
            return JSONResponse(status_code=500, content={"code": "MOCK_SYNC_RESULT_MISSING"})
        return JSONResponse(
            status_code=submission.status_code,
            content=submission.snapshot.result_payload,
        )

    submission_state = "ACCEPTED" if submission.status_code == 202 else "REPLAY"
    data = _northbound_response_data(
        operation_identity,
        idempotency_key,
        validated_payload,
        submission_state=submission_state,
    )
    if submission.status_code == 202 and northbound_operation_store.register_callback_hint(
        operation_identity, idempotency_key
    ):
        # Evidence endpoint 只保留 callback hint 的关联键投影，绝不复制终态或认证字段。
        northbound_callback_hint_evidence[(operation_identity, idempotency_key)] = {
            "callback_type": "WMS_EFFECT_STATUS_HINT",
            "dispatch_key": str(validated_payload.get("dispatch_key") or ""),
            "idempotency_key": idempotency_key,
            "operation_identity": operation_identity,
        }
        background_tasks.add_task(
            _post_callback,
            WES_EXTERNAL_CALLBACK_URL,
            _typed_effect_callback_payload(
                operation_identity=operation_identity,
                idempotency_key=idempotency_key,
                request_payload=validated_payload,
            ),
        )
    return JSONResponse(status_code=submission.status_code, content={"code": submission.status_code, "data": data})


def _northbound_response_data(
    operation_identity: str,
    idempotency_key: str,
    payload: dict[str, Any],
    *,
    submission_state: Literal["ACCEPTED", "IN_PROGRESS_REPLAY", "REPLAY"],
) -> dict[str, Any]:
    """从已校验 typed payload 构造 E08–E14 共用 ACK。"""

    return build_typed_ack(
        operation_identity,
        idempotency_key,
        payload,
        submission_state=submission_state,
    )


def _static_effect_handler(operation_identity: str):
    async def handler(request: Request, background_tasks: BackgroundTasks):
        return await _submit_northbound_effect(
            request=request,
            background_tasks=background_tasks,
            operation_identity=operation_identity,
        )

    handler.__name__ = f"northbound_{operation_identity.replace('.', '_').replace('@', '_')}"
    handler.__wms_operation_identity__ = operation_identity
    return handler


def _typed_query_payload(request: Request, operation_identity: str) -> dict[str, Any]:
    operation = WMS_OPERATION_BY_IDENTITY[operation_identity]
    payload: dict[str, Any] = dict(request.path_params)
    for field_name in operation.request_model.model_fields:
        values = request.query_params.getlist(field_name)
        if values:
            if len(values) > 1:
                payload[field_name] = values
            elif operation.request_model.model_fields[field_name].annotation is int:
                try:
                    payload[field_name] = int(values[0])
                except ValueError:
                    payload[field_name] = values[0]
            elif field_name == "batch_managed" and values[0].lower() in {"true", "false"}:
                payload[field_name] = values[0].lower() == "true"
            else:
                payload[field_name] = values[0]
    return payload


def _static_query_handler(operation_identity: str):
    async def handler(request: Request):
        operation = WMS_OPERATION_BY_IDENTITY[operation_identity]
        raw_path = request.scope["path"]
        query_string = request.scope.get("query_string", b"")
        if query_string:
            raw_path = f"{raw_path}?{query_string.decode('ascii')}"
        body = await _request_body_or_none(request)
        if body is None:
            return Response(status_code=499)
        try:
            verify_status_hmac(request.headers, body, method=request.method, path=raw_path)
            northbound_hmac_replay_guard.consume(
                credential_reference=request.headers["X-WMS-Credential-Reference"],
                timestamp=request.headers["X-WMS-Timestamp"],
                nonce=request.headers["X-WMS-Nonce"],
            )
        except NorthboundAuthError as exc:
            return JSONResponse(status_code=401, content={"code": exc.code})
        if request.method == "GET":
            unknown_query_fields = set(request.query_params) - set(operation.request_model.model_fields)
            if unknown_query_fields:
                return JSONResponse(status_code=422, content={"code": "UNKNOWN_TYPED_QUERY_PARAMETER"})
            payload = _typed_query_payload(request, operation_identity)
        else:
            if request.headers.get("content-type", "").partition(";")[0].strip().lower() != "application/json":
                return JSONResponse(status_code=422, content={"code": "INVALID_TYPED_REQUEST_CONTENT_TYPE"})
            try:
                payload = json.loads(body)
            except (UnicodeDecodeError, json.JSONDecodeError):
                return JSONResponse(status_code=422, content={"code": "INVALID_TYPED_REQUEST"})
        try:
            validated = operation.request_model.model_validate(payload).model_dump(mode="json")
            result = build_typed_result(
                operation_identity,
                validated,
                source_version=0,
                completed_at=datetime.now(UTC).isoformat(),
            )
        except (TypeError, ValueError):
            return JSONResponse(status_code=422, content={"code": "INVALID_TYPED_REQUEST"})
        return JSONResponse(status_code=200, content=result)

    handler.__name__ = f"northbound_{operation_identity.replace('.', '_').replace('@', '_')}"
    handler.__wms_operation_identity__ = operation_identity
    return handler


def _register_frozen_operation_routes() -> None:
    """启动期从唯一 registry 一次性注册 35 条明确 route。"""

    for operation in WMS_OPERATIONS:
        handler_factory = _static_query_handler if operation.mode.value == "QUERY" else _static_effect_handler
        app.add_api_route(
            f"/api/wms{operation.path_template}",
            handler_factory(operation.identity),
            methods=[operation.http_method.value],
            name=f"northbound:{operation.identity}",
        )


_register_frozen_operation_routes()


@app.get("/")
async def root():
    return {"service": "WMS Mock 服务", "version": "1.0.0", "status": "running"}


# ============================================
# 服务器类
# ============================================


class WmsMockServer:
    """WMS Mock 服务器"""

    def __init__(self, host: str = "127.0.0.1", port: int = 8011):
        self.host = host
        self.port = port
        self.config = Config(app=app, host=host, port=port, log_level="info", access_log=False)

    async def start(self) -> None:
        logger.info(f"WMS Mock 服务启动: http://{self.host}:{self.port}")
        server = Server(self.config)
        await server.serve()

    def run(self):
        asyncio.run(self.start())


if __name__ == "__main__":
    server = WmsMockServer()
    server.run()
