"""单 ECS 多设备 Mock 服务。"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

import httpx
from fastapi import BackgroundTasks, Body, FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from uvicorn import Config, Server

try:
    from tests.mock.ecs_mock_catalog import MOCK_ECS_DEVICES, default_success_data
except ModuleNotFoundError:  # Docker 内以 /app/tests/mock 为工作目录直接加载模块。
    from ecs_mock_catalog import MOCK_ECS_DEVICES, default_success_data

DOCKER_APP_ROOT = Path(__file__).resolve().parents[2]
if str(DOCKER_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(DOCKER_APP_ROOT))

from src.workline_runtime.sandbox_catalog import rough_sorter_scan_completed_payload

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


ROUGH_SORTER_SCAN_COMPLETED_DATA: dict[str, Any] = rough_sorter_scan_completed_payload()["data"]

ROUGH_SORTER_STORAGE_RETRY_DATA: dict[str, Any] = {
    "PkgID": "PKG-CAP001-LOT-A-001",
    "business_key": "PKG-CAP001-LOT-A-001",
    "rack_operation": {
        "operation_key": "external:smt_rack_bin:trace-rough-sorter-001:RACK_OPERATION",
        "status": "ARRIVED",
    },
    "active_bin_rack": {
        "rack_id": "RACK-CALLBACK",
        "rack_kind": "SINGLE_LAYER",
        "cells": [
            {
                "bin_code": "BIN-001",
                "bin_cell_index": "4",
                "bin_cell_location": "BIN-001-4",
            }
        ],
    },
    "idempotency_key": "rough-sorter-storage-retry:external:smt_rack_bin:trace-rough-sorter-001:RACK_OPERATION:321",
}


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
    event_id: str | None = Field(default=None, description="供应商事件 ID")
    causation_id: str | None = Field(default=None, description="因果事件 ID")


MockEventRequestBody = Annotated[
    MockEventRequest,
    Body(
        openapi_examples={
            "rough_sorter_scan_completed": {
                "summary": "粗分机扫码完成",
                "description": "触发粗分机入料扫码事件，默认使用 CAP001 / LOT-A / PKG-CAP001-LOT-A-001。",
                "value": {
                    "device_code": "RS-INPUT-ARM-01",
                    "event_type": "SCAN_COMPLETED",
                    "data": ROUGH_SORTER_SCAN_COMPLETED_DATA,
                },
            },
            "rough_sorter_storage_retry": {
                "summary": "粗分机货架到位重试",
                "description": "触发粗分机 WAITING_RACK 阶段后的内部重试事件。",
                "value": {
                    "device_code": "RS-INPUT-ARM-01",
                    "event_type": "ROUGH_SORTER_STORAGE_RETRY",
                    "data": ROUGH_SORTER_STORAGE_RETRY_DATA,
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


async def _report_event(event_payload: dict[str, Any]) -> None:
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
    if event_payload["event_type"] not in device.supported_events:
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
