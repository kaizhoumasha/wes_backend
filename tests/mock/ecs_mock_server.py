"""单 ECS 多设备 Mock 服务。"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import random
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

import httpx
from fastapi import BackgroundTasks, Body, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt
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

ScenarioName = Literal["success", "fail", "timeout", "offline", "manual", "busy"]
TOKEN_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"


class DeviceCommandPayload(BaseModel):
    """WES 下发到 ECS Mock 的设备命令。"""

    model_config = ConfigDict(extra="forbid")

    device_code: str = Field(min_length=1, max_length=100, pattern=TOKEN_PATTERN)
    command_code: str = Field(min_length=1, max_length=160, pattern=TOKEN_PATTERN)
    task_type: str = Field(min_length=1, max_length=160, pattern=TOKEN_PATTERN)
    priority: int = Field(ge=1, le=10)
    timeout: int = Field(gt=0, le=2**31 - 1)
    params: dict[str, Any] = Field(default_factory=dict)
    timestamp: StrictInt = Field(gt=0, le=2**63 - 1)


class DeviceCommandAck(BaseModel):
    """ECS Mock 的 ACK 响应。"""

    code: int
    message: str


class MockEventRequest(BaseModel):
    """设备事件真实 callback payload。"""

    model_config = ConfigDict(extra="forbid")

    device_code: str = Field(min_length=1, description="设备编码")
    event_type: str = Field(min_length=1, description="事件类型")
    timestamp: StrictInt | None = Field(
        default=None,
        description="Unix Epoch 毫秒事件时间。Swagger 调试可不传，Mock 会按发送时刻自动补齐",
    )
    data: dict[str, Any] | None = Field(default=None, description="事件负载数据")


MockEventRequestBody = Annotated[
    MockEventRequest,
    Body(
        openapi_examples={
            "scan_completed": {
                "summary": "设备事件上报",
                "description": "data 的具体业务字段由对应设备合同附录定义。",
                "value": {
                    "device_code": "CAMERA-CONVEYOR-01",
                    "event_type": "SCAN_COMPLETED",
                    "data": {"barcode": "BIN_104"},
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
_SCENARIO_BUSY_COMMAND_CODE = "MOCK-SCENARIO-BUSY"


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


def _apply_scenario(state: DeviceRuntimeState, scenario: ScenarioName) -> None:
    state.scenario = scenario
    state.mode = "AUTO"
    state.status = "IDLE"
    state.is_online = True
    state.current_command_code = None
    state.command_delay_seconds = None
    if scenario == "offline":
        state.is_online = False
    elif scenario == "manual":
        state.mode = "MANUAL"
    elif scenario == "busy":
        state.status = "RUNNING"
        state.current_command_code = _SCENARIO_BUSY_COMMAND_CODE
    state.updated_at = _now_ms()


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

        result_payload: dict[str, Any] = {
            "command_code": payload.command_code,
            "device_code": payload.device_code,
            "result": "SUCCESS",
            "finish_time": _now_ms(),
            "data": default_success_data(payload.device_code, payload.task_type, payload.params),
            "error_detail": None,
        }
        if scenario == "fail":
            error_code = str(payload.params.get("error_code") or "ECS_MOCK_SCENARIO_FAILED")
            result_payload = {
                "command_code": payload.command_code,
                "device_code": payload.device_code,
                "result": "FAILED",
                "finish_time": _now_ms(),
                "data": {},
                "error_detail": {
                    "code": error_code,
                    "msg": "ECS Mock 故障注入失败",
                },
            }

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


def _wire_error(status_code: int, message: str) -> JSONResponse:
    headers = {"Retry-After": "5"} if status_code == 429 else None
    return JSONResponse(
        status_code=status_code,
        content={"code": status_code, "message": message},
        headers=headers,
    )


@app.exception_handler(RequestValidationError)
async def _fixed_wire_validation_error(request: Request, _error: RequestValidationError) -> JSONResponse:
    if request.url.path in {"/api/v1/device/command", "/api/v1/device/status"}:
        return _wire_error(400, "INVALID_ENVELOPE")
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

    semantic_payload = payload.model_dump()
    digest = hashlib.sha256(
        json.dumps(semantic_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    fixed_digest = command_identities.setdefault(payload.command_code, digest)
    if fixed_digest != digest:
        return _wire_error(409, "IDEMPOTENCY_CONFLICT")
    accepted = accepted_commands.get(payload.command_code)
    if accepted is not None:
        return accepted

    device = MOCK_ECS_DEVICES.get(payload.device_code)
    if device is None:
        return _wire_error(404, "DEVICE_NOT_FOUND")
    state = runtime_states[payload.device_code]
    if payload.task_type not in device.supported_commands:
        return _wire_error(422, "ANNEX_VALIDATION_FAILED")
    if state.status == "RUNNING":
        return _wire_error(429, "CAPACITY_EXCEEDED")

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
    ack = DeviceCommandAck(code=200, message="Accepted")
    accepted_commands[payload.command_code] = ack
    return ack


@app.get("/api/v1/device/status", response_model=None)
async def get_device_status(
    device_code: Annotated[str | None, Query(min_length=1, max_length=100, pattern=TOKEN_PATTERN)] = None,
) -> JSONResponse:
    """按供应商现行 wire 返回一个或全部设备状态。"""

    if device_code is not None and device_code not in MOCK_ECS_DEVICES:
        return _wire_error(404, "DEVICE_NOT_FOUND")
    selected_codes = (device_code,) if device_code is not None else tuple(MOCK_ECS_DEVICES)
    observed_at = _now_ms()
    devices = []
    for selected_code in selected_codes:
        device = MOCK_ECS_DEVICES[selected_code]
        state = runtime_states[selected_code]
        devices.append(
            {
                "device": {
                    "device_code": device.device_code,
                    "device_name": device.device_name,
                    "device_type": device.device_type,
                    "role": device.role,
                    "supported_commands": device.supported_commands,
                    "supported_events": device.supported_events,
                },
                "state": {
                    "device_code": state.device_code,
                    "mode": state.mode,
                    "status": state.status,
                    "is_online": state.is_online,
                    "current_command_code": state.current_command_code,
                    "scenario": state.scenario,
                    "updated_at": observed_at,
                },
            }
        )
    return JSONResponse(content={"devices": devices})


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
    if state.status == "RUNNING" and state.current_command_code != _SCENARIO_BUSY_COMMAND_CODE:
        raise HTTPException(status_code=409, detail=f"Device has an active command: {state.current_command_code}")
    _apply_scenario(state, payload.scenario)
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
