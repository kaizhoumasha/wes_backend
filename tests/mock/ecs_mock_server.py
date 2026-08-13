"""单 ECS 多设备 Mock 服务。"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import random
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from fastapi import BackgroundTasks, Body, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from starlette.exceptions import HTTPException as StarletteHTTPException
from uvicorn import Config, Server

try:
    from tests.mock.ecs_mock_catalog import MOCK_ECS_DEVICES, default_success_data
except ModuleNotFoundError:  # Docker 内以 /app/tests/mock 为工作目录直接加载模块。
    from ecs_mock_catalog import MOCK_ECS_DEVICES, default_success_data

DOCKER_APP_ROOT = Path(__file__).resolve().parents[2]
if str(DOCKER_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(DOCKER_APP_ROOT))

from src.app.callback.contracts.runtime_events import is_platform_control_event

logger = logging.getLogger(__name__)

WES_RESULT_CALLBACK_URL = os.getenv(
    "WES_RESULT_CALLBACK_URL",
    "http://localhost:8001/api/v1/callback/result",
)
WES_EVENT_CALLBACK_URL = os.getenv(
    "WES_EVENT_CALLBACK_URL",
    "http://localhost:8001/api/v1/callback/event",
)
API_APP_ID = os.getenv("API_APP_ID", "app_Gqnvr3dpjGwlrjtO")
API_APP_SECRET = os.getenv("API_APP_SECRET", "sec_fqYNIij1ZD8aekbn0AONhk_H7VAzj5gEpcMC9d__tao")
_COMMAND_EXECUTION_DELAY_SECONDS_RAW = os.getenv("ECS_MOCK_COMMAND_DELAY_SECONDS")
COMMAND_EXECUTION_DELAY_SECONDS = (
    float(_COMMAND_EXECUTION_DELAY_SECONDS_RAW) if _COMMAND_EXECUTION_DELAY_SECONDS_RAW else None
)
COMMAND_DELAY_MIN_SECONDS = 2.0
COMMAND_DELAY_MAX_SECONDS = 8.0

ScenarioName = Literal["success", "fail", "timeout"]
TOKEN_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"


class DeviceCommandPayload(BaseModel):
    """WES 下发到 ECS Mock 的设备命令。"""

    model_config = ConfigDict(extra="forbid")

    device_code: str = Field(min_length=1, max_length=100, pattern=TOKEN_PATTERN)
    command_code: str = Field(min_length=1, max_length=160, pattern=TOKEN_PATTERN)
    contract_key: str = Field(min_length=1, max_length=100, pattern=TOKEN_PATTERN)
    contract_version: str = Field(min_length=1, max_length=40, pattern=TOKEN_PATTERN)
    task_type: str = Field(min_length=1, max_length=160, pattern=TOKEN_PATTERN)
    params: dict[str, Any] = Field(default_factory=dict)
    timestamp: StrictInt = Field(gt=0, le=2**63 - 1)
    trace_id: str | None = Field(default=None, min_length=1, max_length=120, pattern=TOKEN_PATTERN)

    @model_validator(mode="after")
    def reject_explicit_null_trace(self) -> DeviceCommandPayload:
        if "trace_id" in self.model_fields_set and self.trace_id is None:
            raise ValueError("trace_id 不可显式为 null")
        return self


class DeviceCommandAck(BaseModel):
    """ECS Mock 的 ACK 响应。"""

    code: int
    message: str
    trace_id: str | None = None


class MockEventRequest(BaseModel):
    """设备事件真实 callback payload。"""

    model_config = ConfigDict(extra="forbid")

    device_code: str = Field(min_length=1, description="设备编码")
    event_type: str = Field(min_length=1, description="事件类型")
    timestamp: int | None = Field(
        default=None,
        description="事件时间戳（Unix 时间戳，毫秒）。Swagger 调试可不传，Mock 会按发送时刻自动补齐",
    )
    data: dict[str, Any] | None = Field(default=None, description="事件负载数据")
    trace_id: str | None = Field(default=None, description="统一 Trace ID")


MockEventRequestBody = Annotated[
    MockEventRequest,
    Body(
        openapi_examples={
            "scan_completed": {
                "summary": "设备扫码完成",
                "description": "触发通用设备扫码事件。",
                "value": {
                    "device_code": "CAMERA-CONVEYOR-01",
                    "event_type": "SCAN_COMPLETED",
                    "data": {"barcode": "PKG-001"},
                },
            },
        },
    ),
]


class ScenarioRequest(BaseModel):
    """设备故障注入场景。"""

    scenario: ScenarioName


class DeviceRuntimeState(BaseModel):
    """Mock 设备运行态。"""

    device_code: str
    mode: str = "AUTO"
    status: str = "IDLE"
    is_online: bool = True
    current_command_code: str | None = None
    scenario: ScenarioName = "success"
    command_delay_seconds: float | None = Field(default=None, ge=0)
    updated_at: int


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def _calculate_signature(app_secret: str, app_id: str, timestamp: str, method: str, path: str) -> str:
    sign_string = f"{app_id}{timestamp}{method}{path}"
    return hmac.new(app_secret.encode("utf-8"), sign_string.encode("utf-8"), hashlib.sha256).hexdigest()


def _build_api_auth_headers(method: str, path: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    signature = _calculate_signature(API_APP_SECRET, API_APP_ID, timestamp, method, path)
    return {
        "X-App-ID": API_APP_ID,
        "X-Timestamp": timestamp,
        "X-Signature": signature,
    }


def _random_command_delay_seconds() -> float:
    return round(random.uniform(COMMAND_DELAY_MIN_SECONDS, COMMAND_DELAY_MAX_SECONDS), 3)


def _initial_state() -> dict[str, DeviceRuntimeState]:
    return {
        device_code: DeviceRuntimeState(device_code=device_code, updated_at=_now_ms())
        for device_code in MOCK_ECS_DEVICES
    }


runtime_states: dict[str, DeviceRuntimeState] = _initial_state()
command_history: list[dict[str, Any]] = []
command_identities: dict[str, str] = {}
accepted_commands: dict[str, DeviceCommandAck] = {}
event_history: list[dict[str, Any]] = []


def reset_mock_state() -> None:
    """重置测试态，供 pytest 用例隔离状态。"""

    runtime_states.clear()
    runtime_states.update(_initial_state())
    command_history.clear()
    accepted_commands.clear()
    command_identities.clear()
    event_history.clear()


def _get_state_or_400(device_code: str) -> DeviceRuntimeState:
    state = runtime_states.get(device_code)
    if state is None:
        raise HTTPException(status_code=400, detail=f"Unknown device_code: {device_code}")
    return state


def _command_delay_seconds_for_command() -> float:
    if COMMAND_EXECUTION_DELAY_SECONDS is not None:
        return COMMAND_EXECUTION_DELAY_SECONDS
    return _random_command_delay_seconds()


async def _post_callback(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    parsed_url = urlparse(url)
    headers = _build_api_auth_headers("POST", parsed_url.path)
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()


async def _post_event_callback_for_debug(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    parsed_url = urlparse(url)
    headers = _build_api_auth_headers("POST", parsed_url.path)
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(url, json=payload, headers=headers)

    try:
        response_body: Any = response.json()
    except ValueError:
        response_body = {"raw": response.text}

    return {
        "http_status": response.status_code,
        "body": response_body,
    }


async def _finish_command(payload: DeviceCommandPayload, delay_seconds: float) -> None:
    state = _get_state_or_400(payload.device_code)
    scenario = state.scenario
    try:
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)

        if scenario == "timeout":
            logger.info("ECS Mock 超时场景不回调: command_code=%s", payload.command_code)
            return

        source_event_id = f"RESULT-{hashlib.sha256(payload.command_code.encode()).hexdigest()}"
        result_payload: dict[str, Any] = {
            "command_code": payload.command_code,
            "device_code": payload.device_code,
            "contract_key": payload.contract_key,
            "contract_version": payload.contract_version,
            "result": "SUCCESS",
            "finish_time": _now_ms(),
            "source_event_id": source_event_id,
            "data": default_success_data(payload.device_code, payload.task_type, payload.params),
            "error_detail": None,
        }
        if payload.trace_id is not None:
            result_payload["trace_id"] = payload.trace_id
        if scenario == "fail":
            error_code = str(payload.params.get("error_code") or "ECS_MOCK_SCENARIO_FAILED")
            result_payload = {
                "command_code": payload.command_code,
                "device_code": payload.device_code,
                "contract_key": payload.contract_key,
                "contract_version": payload.contract_version,
                "result": "FAILED",
                "finish_time": _now_ms(),
                "source_event_id": source_event_id,
                "data": {},
                "error_detail": {
                    "code": error_code,
                    "message": "ECS Mock 故障注入失败",
                },
            }
            if payload.trace_id is not None:
                result_payload["trace_id"] = payload.trace_id

        await _post_callback(WES_RESULT_CALLBACK_URL, result_payload)
    finally:
        if scenario != "success" and state.scenario == scenario:
            state.scenario = "success"
        state.status = "IDLE"
        state.current_command_code = None
        state.command_delay_seconds = None
        state.updated_at = _now_ms()


async def _report_event(event_payload: dict[str, Any]) -> None:
    await _post_callback(WES_EVENT_CALLBACK_URL, event_payload)
    event_history.append(event_payload)


app = FastAPI(
    title="ECS Mock 服务",
    description="一个 ECS Mock 服务管理多台测试设备",
    version="1.0.0",
)


class FixedWireBodyLimitMiddleware:
    """在 Starlette 缓冲请求体前限制固定命令端点。"""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope["path"] != "/api/v1/device/command":
            await self.app(scope, receive, send)
            return
        messages: list[dict[str, Any]] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            messages.append(message)
            if message["type"] == "http.request":
                size += len(message.get("body", b""))
                if size > 256 * 1024:
                    await _wire_error(413, "PAYLOAD_TOO_LARGE")(scope, receive, send)
                    return
                if not message.get("more_body", False):
                    break

        async def replay_receive():
            if messages:
                return messages.pop(0)
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, replay_receive, send)


app.add_middleware(FixedWireBodyLimitMiddleware)


def _wire_error(status_code: int, message: str, trace_id: str | None = None) -> JSONResponse:
    headers = {"Retry-After": "5"} if status_code == 429 else None
    return JSONResponse(
        status_code=status_code,
        content={"code": status_code, "message": message, **({"trace_id": trace_id} if trace_id is not None else {})},
        headers=headers,
    )


@app.exception_handler(RequestValidationError)
async def _fixed_wire_validation_error(request: Request, _error: RequestValidationError) -> JSONResponse:
    if request.url.path in {"/api/v1/device/command", "/api/v1/device/status"}:
        body = _error.body
        trace_id = body.get("trace_id") if isinstance(body, dict) else None
        if not isinstance(trace_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,119}", trace_id):
            trace_id = None
        return _wire_error(400, "INVALID_ENVELOPE", trace_id)
    return JSONResponse(status_code=422, content={"detail": _error.errors()})


@app.exception_handler(StarletteHTTPException)
async def _fixed_wire_http_error(request: Request, error: StarletteHTTPException) -> JSONResponse:
    if request.url.path in {"/api/v1/device/command", "/api/v1/device/status"} and error.status_code == 405:
        return _wire_error(405, "METHOD_NOT_ALLOWED")
    return JSONResponse(status_code=error.status_code, content={"detail": error.detail})


@app.post("/api/v1/device/command", response_model=DeviceCommandAck, response_model_exclude_none=True)
async def receive_command(
    payload: DeviceCommandPayload, background_tasks: BackgroundTasks
) -> DeviceCommandAck | JSONResponse:
    """接收 WES 下发命令，立即 ACK 并后台回调执行结果。"""

    semantic_payload = payload.model_dump(exclude={"trace_id"})
    digest = hashlib.sha256(
        json.dumps(semantic_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    fixed_digest = command_identities.setdefault(payload.command_code, digest)
    if fixed_digest != digest:
        return _wire_error(409, "IDEMPOTENCY_CONFLICT", payload.trace_id)
    accepted = accepted_commands.get(payload.command_code)
    if accepted is not None:
        return DeviceCommandAck(code=accepted.code, message=accepted.message, trace_id=payload.trace_id)

    device = MOCK_ECS_DEVICES.get(payload.device_code)
    if device is None:
        return _wire_error(404, "DEVICE_NOT_FOUND", payload.trace_id)
    state = runtime_states[payload.device_code]
    if payload.task_type not in device.supported_commands:
        return _wire_error(422, "ANNEX_VALIDATION_FAILED", payload.trace_id)
    if payload.contract_key != device.contract_key or payload.contract_version != device.contract_version:
        return _wire_error(422, "ANNEX_VALIDATION_FAILED", payload.trace_id)
    if state.status == "RUNNING":
        return _wire_error(429, "CAPACITY_EXCEEDED", payload.trace_id)

    state.status = "RUNNING"
    state.current_command_code = payload.command_code
    state.updated_at = _now_ms()
    delay_seconds = _command_delay_seconds_for_command()
    state.command_delay_seconds = delay_seconds
    command_record = payload.model_dump()
    command_record["current_command_code"] = payload.command_code
    command_record["command_delay_seconds"] = delay_seconds
    command_history.append(command_record)
    background_tasks.add_task(_finish_command, payload, delay_seconds)
    ack = DeviceCommandAck(code=200, message="ACCEPTED", trace_id=payload.trace_id)
    accepted_commands[payload.command_code] = ack
    return ack


@app.get("/api/v1/device/status", response_model=None)
async def get_device_status(
    device_code: Annotated[str, Query(min_length=1, max_length=100, pattern=TOKEN_PATTERN)],
) -> JSONResponse:
    """按 uniform wire 返回单设备扁平状态。"""

    device = MOCK_ECS_DEVICES.get(device_code)
    if device is None:
        return _wire_error(404, "DEVICE_NOT_FOUND")
    state = runtime_states[device_code]
    return JSONResponse(
        content={
            "device_code": device_code,
            "contract_key": device.contract_key,
            "contract_version": device.contract_version,
            "mode": state.mode,
            "status": state.status,
            "current_command_code": state.current_command_code,
            "error_detail": None,
            "timestamp": state.updated_at,
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/v1/mock/commands")
async def list_mock_commands(
    device_code: str | None = None,
    task_type: str | None = None,
    command_code: str | None = None,
    limit: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    """查询 ECS Mock 已接收的 WES 设备命令，供 Swagger 调试确认后续下发。"""

    commands = command_history
    if device_code:
        commands = [command for command in commands if command.get("device_code") == device_code]
    if task_type:
        commands = [command for command in commands if command.get("task_type") == task_type]
    if command_code:
        commands = [command for command in commands if command.get("command_code") == command_code]

    return {
        "code": 200,
        "message": "OK",
        "data": {
            "total": len(commands),
            "commands": list(reversed(commands[-limit:])),
        },
    }


@app.post("/api/v1/mock/event")
async def report_mock_event(payload: MockEventRequestBody) -> dict[str, Any]:
    """按真实 callback payload 手动上报设备事件，供 Swagger UI 调试工作线流程。"""

    event_payload = payload.model_dump(exclude_none=True)
    event_payload.setdefault("timestamp", _now_ms())
    _ = _get_state_or_400(event_payload["device_code"])
    device = MOCK_ECS_DEVICES[event_payload["device_code"]]
    event_payload["contract_key"] = device.contract_key
    event_payload["contract_version"] = device.contract_version
    event_payload["source_event_id"] = f"EVENT-{uuid4()}"
    if (
        not is_platform_control_event(event_payload["event_type"])
        and event_payload["event_type"] not in device.supported_events
    ):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported event_type for {event_payload['device_code']}: {event_payload['event_type']}",
        )
    delivery = await _post_event_callback_for_debug(WES_EVENT_CALLBACK_URL, event_payload)
    event_history.append(event_payload)
    delivered = 200 <= delivery["http_status"] < 300
    return {
        "code": 200 if delivered else 502,
        "message": "Event delivered" if delivered else "WES callback failed",
        "data": {
            "device_code": event_payload["device_code"],
            "event_type": event_payload["event_type"],
            "timestamp": event_payload["timestamp"],
            "wes_http_status": delivery["http_status"],
            "wes_response": delivery["body"],
        },
    }


@app.post("/api/v1/mock/devices/{device_code}/scenario")
async def set_device_scenario(device_code: str, payload: ScenarioRequest) -> dict[str, Any]:
    """设置设备的最小故障注入场景。"""

    state = _get_state_or_400(device_code)
    state.scenario = payload.scenario
    state.updated_at = _now_ms()
    return {"code": 200, "message": "Scenario updated", "data": state.model_dump()}


@app.get("/")
async def root() -> dict[str, Any]:
    return {"service": "ECS Mock 服务", "version": "1.0.0", "status": "running", "port": 8010}


class EcsMockServer:
    """ECS Mock 服务器。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 8010):
        self.host = host
        self.port = port
        self._server: Server | None = None
        self.config = Config(app=app, host=host, port=port, log_level="info")

    async def start(self) -> None:
        self._server = Server(self.config)
        await self._server.serve()  # type: ignore[misc]

    def run(self) -> None:
        asyncio.run(self.start())


if __name__ == "__main__":
    EcsMockServer(host="0.0.0.0").run()
