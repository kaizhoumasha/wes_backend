"""
SMT 粗分机 AGV Mock 服务

正式接口:
- POST /api/v1/device/command
- GET  /api/v1/device/status
- POST /api/v1/device/cancel

调试接口:
- POST /debug/execute
- POST /debug/mode
- GET  /debug/executions
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Literal, cast

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from uvicorn import Config, Server

project_root = Path(__file__).parent.parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from tests.mock.smt_classifier.mock_support import (
    WES_EXTERNAL_CALLBACK_URL,
    CancelRequest,
    DeviceCommandAck,
    DeviceStatusResponse,
    JsonDict,
    current_millis,
    post_signed_json,
)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

DEVICE_CODE = os.getenv("DEVICE_CODE", "AGV01")
DEVICE_PORT = int(os.getenv("DEVICE_PORT", "8009"))
EXECUTION_TIME = float(os.getenv("AGV_EXECUTION_TIME", "2.0"))
AGV_MODE = os.getenv("AGV_MODE", "success")

DEVICE_STATUS = {
    "device_code": DEVICE_CODE,
    "status": "IDLE",
    "is_online": True,
    "error_code": "NONE",
}


class ExecutionRecord(BaseModel):
    execution_id: str
    command_code: str
    task_type: str
    result: str
    callback_url: str
    correlation_id: str
    started_at: datetime
    finished_at: datetime | None = None


class AgvDebugExecuteRequest(BaseModel):
    command_code: str = "AGV-DEBUG-001"
    task_type: str = "MOVE_RACK"
    callback_url: str = WES_EXTERNAL_CALLBACK_URL
    callback_type: str = "AGV_TASK_RESULT"
    correlation_id: str = "debug-corr-001"
    params: JsonDict = Field(default_factory=dict)


class AgvDebugModeRequest(BaseModel):
    mode: Literal["success", "fail", "timeout"]


class AgvCommandPayload(BaseModel):
    command_code: str
    task_type: str
    priority: int = 5
    timeout: int = 300000
    params: JsonDict = Field(default_factory=dict)
    timestamp: int
    correlation_id: str
    callback_type: str = "AGV_TASK_RESULT"
    callback_url: str = WES_EXTERNAL_CALLBACK_URL


class AgvSimulator:
    def __init__(self, mode: str = AGV_MODE):
        self.mode = mode
        self.executions: list[ExecutionRecord] = []

    async def execute_command(self, payload: AgvCommandPayload) -> ExecutionRecord:
        callback_url = payload.callback_url
        callback_type = payload.callback_type
        correlation_id = payload.correlation_id
        if not correlation_id:
            raise HTTPException(status_code=400, detail="correlation_id is required")

        record = ExecutionRecord(
            execution_id=f"EXEC-{datetime.now().strftime('%Y%m%d%H%M%S')}-{len(self.executions) + 1:03d}",
            command_code=payload.command_code,
            task_type=payload.task_type,
            result="PENDING",
            callback_url=callback_url,
            correlation_id=correlation_id,
            started_at=datetime.now(),
        )
        self.executions.append(record)

        if self.mode == "timeout":
            return record

        await asyncio.sleep(float((payload.params or {}).get("execution_time", EXECUTION_TIME)))
        result = "FAILED" if self.mode == "fail" else "SUCCESS"
        await self._callback_to_wes(
            callback_url=callback_url,
            callback_type=callback_type,
            correlation_id=correlation_id,
            command_code=payload.command_code,
            result=result,
            params=payload.params or {},
        )
        record.result = result
        record.finished_at = datetime.now()
        return record

    async def _callback_to_wes(
        self,
        *,
        callback_url: str,
        callback_type: str,
        correlation_id: str,
        command_code: str,
        result: str,
        params: JsonDict,
    ) -> None:
        payload: JsonDict = {
            "callback_type": callback_type,
            "correlation_id": correlation_id,
            "command_code": command_code,
            "result": result,
            "finish_time": current_millis(),
            "data": {
                "from_location": params.get("from_location"),
                "to_location": params.get("to_location"),
                "rack_type": params.get("rack_type"),
            },
        }
        if result == "FAILED":
            payload["message"] = "AGV delivery failed"
        await post_signed_json(callback_url, payload)


agv_simulator = AgvSimulator()
current_command: JsonDict | None = None
current_command_task: asyncio.Task[None] | None = None
app = FastAPI(
    title="SMT 粗分机 AGV Mock 服务",
    description="模拟 AGV 搬运正式接口与调试接口",
    version="1.0.0",
)


@app.get("/api/v1/device/status", response_model=DeviceStatusResponse)
async def get_device_status() -> DeviceStatusResponse:
    return DeviceStatusResponse(
        device_code=DEVICE_CODE,
        status=cast("Literal['IDLE', 'RUNNING', 'ERROR', 'OFFLINE']", DEVICE_STATUS["status"]),
        current_command_code=current_command["command_code"] if current_command else None,
        error_code=cast("str", DEVICE_STATUS["error_code"]),
        timestamp=current_millis(),
    )


@app.post("/api/v1/device/command", response_model=DeviceCommandAck)
async def receive_command(payload: AgvCommandPayload) -> DeviceCommandAck:
    global current_command, current_command_task

    if DEVICE_STATUS["status"] == "RUNNING":
        raise HTTPException(status_code=503, detail="Device Busy")
    if payload.task_type != "MOVE_RACK":
        raise HTTPException(status_code=400, detail="AGV mock only supports MOVE_RACK")

    DEVICE_STATUS["status"] = "RUNNING"
    DEVICE_STATUS["error_code"] = "NONE"
    current_command = cast("JsonDict", payload.model_dump())
    current_command_task = asyncio.create_task(_execute_with_cleanup(payload))
    return DeviceCommandAck(code=200, message="Accepted", trace_id=f"{DEVICE_CODE}-{payload.command_code}")


async def _execute_with_cleanup(payload: AgvCommandPayload) -> None:
    global current_command, current_command_task
    try:
        await agv_simulator.execute_command(payload)
    except asyncio.CancelledError:
        logger.info("AGV 指令已中断: %s", payload.command_code)
        raise
    finally:
        DEVICE_STATUS["status"] = "IDLE"
        current_command = None
        current_command_task = None


@app.post("/api/v1/device/cancel", response_model=DeviceCommandAck)
async def cancel_command(request: CancelRequest) -> DeviceCommandAck:
    global current_command, current_command_task

    if current_command is None:
        raise HTTPException(status_code=404, detail="No Active Command")
    if current_command["command_code"] != request.command_code:
        raise HTTPException(status_code=404, detail="Command Not Found")
    task = current_command_task
    if task is None or task.done():
        DEVICE_STATUS["status"] = "IDLE"
        current_command = None
        current_command_task = None
        raise HTTPException(status_code=409, detail="Command Already Finished")
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return DeviceCommandAck(code=200, message="Cancelled")


@app.post("/debug/execute", response_model=ExecutionRecord)
async def debug_execute(request: AgvDebugExecuteRequest) -> ExecutionRecord:
    payload = AgvCommandPayload(
        command_code=request.command_code,
        task_type=request.task_type,
        priority=5,
        timeout=300000,
        params=request.params,
        timestamp=current_millis(),
        correlation_id=request.correlation_id,
        callback_type=request.callback_type,
        callback_url=request.callback_url,
    )
    return await agv_simulator.execute_command(payload)


@app.post("/debug/mode")
async def debug_mode(request: AgvDebugModeRequest) -> JsonDict:
    agv_simulator.mode = request.mode
    return {"status": "updated", "mode": agv_simulator.mode}


@app.get("/debug/executions", response_model=list[ExecutionRecord])
async def debug_executions(limit: int = 50) -> list[ExecutionRecord]:
    return agv_simulator.executions[-limit:]


@app.get("/")
async def root() -> JsonDict:
    return {
        "service": "agv_mock",
        "device_code": DEVICE_CODE,
        "mode": agv_simulator.mode,
        "callback_url": WES_EXTERNAL_CALLBACK_URL,
    }


class AgvMockServer:
    def __init__(self, host: str = "127.0.0.1", port: int = DEVICE_PORT):
        self.host = host
        self.port = port
        self.config = Config(app=app, host=host, port=port, log_level="info")
        self._server: Server | None = None

    async def start(self) -> None:
        logger.info("SMT AGV Mock 服务启动: http://%s:%s", self.host, self.port)
        logger.info("正式接口:")
        logger.info("  - GET  /api/v1/device/status")
        logger.info("  - POST /api/v1/device/command")
        logger.info("  - POST /api/v1/device/cancel")
        logger.info("调试接口:")
        logger.info("  - POST /debug/execute")
        logger.info("  - POST /debug/mode")
        logger.info("  - GET  /debug/executions")
        self._server = Server(self.config)
        await self._server.serve()

    def run(self) -> None:
        asyncio.run(self.start())


if __name__ == "__main__":
    AgvMockServer().run()
