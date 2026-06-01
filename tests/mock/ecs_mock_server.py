"""单 ECS 多设备 Mock 服务。"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import time
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field
from uvicorn import Config, Server

try:
    from tests.mock.ecs_mock_catalog import MOCK_ECS_DEVICES, default_success_data
except ModuleNotFoundError:  # Docker 内以 /app/tests/mock 为工作目录直接加载模块。
    from ecs_mock_catalog import MOCK_ECS_DEVICES, default_success_data

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
COMMAND_EXECUTION_DELAY_SECONDS = float(os.getenv("ECS_MOCK_COMMAND_DELAY_SECONDS", "0.05"))

ScenarioName = Literal["success", "fail", "timeout"]


class DeviceCommandPayload(BaseModel):
    """WES 下发到 ECS Mock 的设备命令。"""

    device_code: str = Field(min_length=1)
    command_code: str = Field(min_length=1)
    task_type: str = Field(min_length=1)
    priority: int = 5
    timeout: int = 30000
    params: dict[str, Any] = Field(default_factory=dict)
    timestamp: int | None = None


class DeviceCommandAck(BaseModel):
    """ECS Mock 的 ACK 响应。"""

    code: int
    message: str
    trace_id: str | None = None


class MockEventRequest(BaseModel):
    """手动上报设备事件。"""

    device_code: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: int | None = None


class ScenarioRequest(BaseModel):
    """设备故障注入场景。"""

    scenario: ScenarioName


class DeviceRuntimeState(BaseModel):
    """Mock 设备运行态。"""

    device_code: str
    status: str = "IDLE"
    is_online: bool = True
    current_command_code: str | None = None
    scenario: ScenarioName = "success"
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


def _initial_state() -> dict[str, DeviceRuntimeState]:
    return {
        device_code: DeviceRuntimeState(device_code=device_code, updated_at=_now_ms())
        for device_code in MOCK_ECS_DEVICES
    }


runtime_states: dict[str, DeviceRuntimeState] = _initial_state()
command_history: list[dict[str, Any]] = []
event_history: list[dict[str, Any]] = []


def reset_mock_state() -> None:
    """重置测试态，供 pytest 用例隔离状态。"""

    runtime_states.clear()
    runtime_states.update(_initial_state())
    command_history.clear()
    event_history.clear()


def _get_state_or_400(device_code: str) -> DeviceRuntimeState:
    state = runtime_states.get(device_code)
    if state is None:
        raise HTTPException(status_code=400, detail=f"Unknown device_code: {device_code}")
    return state


async def _post_callback(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    parsed_url = urlparse(url)
    headers = _build_api_auth_headers("POST", parsed_url.path)
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()


async def _finish_command(payload: DeviceCommandPayload) -> None:
    state = _get_state_or_400(payload.device_code)
    scenario = state.scenario
    try:
        if COMMAND_EXECUTION_DELAY_SECONDS > 0:
            await asyncio.sleep(COMMAND_EXECUTION_DELAY_SECONDS)

        if scenario == "timeout":
            logger.info("ECS Mock 超时场景不回调: command_code=%s", payload.command_code)
            return

        result_payload: dict[str, Any] = {
            "command_code": payload.command_code,
            "device_code": payload.device_code,
            "result": "SUCCESS",
            "finish_time": _now_ms(),
            "data": default_success_data(payload.device_code, payload.task_type, payload.params),
        }
        if scenario == "fail":
            error_code = str(payload.params.get("error_code") or "ECS_MOCK_SCENARIO_FAILED")
            result_payload = {
                "command_code": payload.command_code,
                "device_code": payload.device_code,
                "result": "FAILED",
                "finish_time": _now_ms(),
                "error_detail": {
                    "code": error_code,
                    "message": "ECS Mock 故障注入失败",
                },
            }

        await _post_callback(WES_RESULT_CALLBACK_URL, result_payload)
    finally:
        if scenario != "success" and state.scenario == scenario:
            state.scenario = "success"
        state.status = "IDLE"
        state.current_command_code = None
        state.updated_at = _now_ms()


async def _report_event(payload: MockEventRequest) -> None:
    event_payload = {
        "device_code": payload.device_code,
        "event_type": payload.event_type,
        "timestamp": payload.timestamp or _now_ms(),
        "data": payload.data,
    }
    await _post_callback(WES_EVENT_CALLBACK_URL, event_payload)
    event_history.append(event_payload)


app = FastAPI(
    title="ECS Mock 服务",
    description="一个 ECS Mock 服务管理多台测试设备",
    version="1.0.0",
)


@app.post("/api/v1/device/command", response_model=DeviceCommandAck)
async def receive_command(payload: DeviceCommandPayload, background_tasks: BackgroundTasks) -> DeviceCommandAck:
    """接收 WES 下发命令，立即 ACK 并后台回调执行结果。"""

    device = MOCK_ECS_DEVICES.get(payload.device_code)
    state = _get_state_or_400(payload.device_code)
    if device is None:
        raise HTTPException(status_code=400, detail=f"Unknown device_code: {payload.device_code}")
    if payload.task_type not in device.supported_commands:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported task_type for {payload.device_code}: {payload.task_type}",
        )
    if state.status == "RUNNING":
        raise HTTPException(status_code=503, detail="Device Busy")

    state.status = "RUNNING"
    state.current_command_code = payload.command_code
    state.updated_at = _now_ms()
    command_history.append(payload.model_dump())
    background_tasks.add_task(_finish_command, payload)
    return DeviceCommandAck(code=200, message="Accepted", trace_id=f"ECS-MOCK-{payload.command_code}")


@app.get("/api/v1/device/status")
async def get_device_status(device_code: str | None = None) -> dict[str, Any]:
    """查询单设备或全部设备状态。"""

    if device_code:
        device = MOCK_ECS_DEVICES.get(device_code)
        state = _get_state_or_400(device_code)
        return {"device": device, "state": state}

    return {
        "devices": [
            {"device": MOCK_ECS_DEVICES[device_code], "state": runtime_states[device_code]}
            for device_code in sorted(MOCK_ECS_DEVICES)
        ]
    }


@app.post("/api/v1/mock/event")
async def report_mock_event(payload: MockEventRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    """手动上报设备事件，替代旧 sensor trigger API。"""

    _ = _get_state_or_400(payload.device_code)
    device = MOCK_ECS_DEVICES[payload.device_code]
    if payload.event_type not in device.supported_events:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported event_type for {payload.device_code}: {payload.event_type}",
        )
    background_tasks.add_task(_report_event, payload)
    return {"code": 200, "message": "Event accepted", "data": {"device_code": payload.device_code}}


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
