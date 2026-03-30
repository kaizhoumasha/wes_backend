"""
SMT 粗分机流水线 Mock 服务

单线模式下仅模拟 PIPELINE01，负责流水线进料位到出料位的传输。

正式接口:
- POST /api/v1/device/command
- GET  /api/v1/device/status
- POST /api/v1/device/cancel

调试接口:
- /debug/*
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from uvicorn import Config, Server

project_root = Path(__file__).parent.parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from tests.mock.smt_classifier.mock_support import (  # noqa: E402
    WES_RESULT_CALLBACK_URL,
    CancelRequest,
    DeviceCommandAck,
    DeviceCommandPayload,
    DeviceLocation,
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

DEVICE_CODE = os.getenv("DEVICE_CODE", "PIPELINE01")
EXECUTION_TIME = float(os.getenv("EXECUTION_TIME", "1.5"))
AUTO_TRIGGER_INTERVAL = int(os.getenv("PIPELINE_AUTO_TRIGGER_DEFAULT_INTERVAL", "5"))

DEVICE_INFO = {
    "device_code": "PIPELINE01",
    "device_name": "SMT 粗分机流水线",
    "device_type": "PIPELINE",
    "port": 8005,
    "description": "负责进料位到出料位的传输",
    "task_types": ["MOVE_FORWARD"],
    "source": DeviceLocation(location_id="STATION_PIPELINE1_INPUT1", location_type="PIPELINE_PLATFORM"),
    "target": DeviceLocation(location_id="STATION_PIPELINE1_OUTPUT1", location_type="PIPELINE_PLATFORM"),
}


class ExecutionRecord(BaseModel):
    execution_id: str
    command_code: str
    task_type: str
    source: JsonDict
    target: JsonDict
    result: str
    message: str
    started_at: datetime
    finished_at: datetime
    duration_ms: int


class PipelineStatusResponse(BaseModel):
    device_code: str
    status: str
    current_command_code: str | None = None
    error_code: str = "NONE"
    is_auto_executing: bool
    execution_count: int
    success_count: int
    failure_count: int
    timestamp: int


class PipelineRootResponse(BaseModel):
    service: str
    version: str
    status: Literal["running"]
    device_code: str
    device_name: str
    description: str
    task_types: list[str]
    device_status: PipelineStatusResponse
    wes_result_callback_url: str


class ManualExecuteRequest(BaseModel):
    task_type: str = "MOVE_FORWARD"
    source_type: str | None = "PIPELINE_PLATFORM"
    target_type: str | None = "PIPELINE_PLATFORM"
    source_location_id: str | None = None
    target_location_id: str | None = None
    command_code: str | None = None
    simulate_failure: bool = False
    execution_time: float = EXECUTION_TIME
    report_result: bool = False


class AutoExecuteConfig(BaseModel):
    interval_seconds: int = AUTO_TRIGGER_INTERVAL
    max_executions: int | None = None


class AutoExecuteStartResponse(BaseModel):
    status: Literal["started"]
    device_code: str
    interval_seconds: int
    max_executions: int | None = None


class AutoExecuteStopResponse(BaseModel):
    status: Literal["stopped"]
    device_code: str
    execution_count: int


DEVICE_STATUS = {
    "device_code": DEVICE_INFO["device_code"],
    "status": "IDLE",
    "error_code": "NONE",
    "is_online": True,
}


class PipelineSimulator:
    def __init__(self, device_code: str = DEVICE_INFO["device_code"]):
        self.device_code = device_code
        self._counter = 0
        self._execution_count = 0
        self._success_count = 0
        self._failure_count = 0
        self._executions: list[ExecutionRecord] = []
        self._is_auto_executing = False
        self._auto_task: asyncio.Task[None] | None = None
        self._auto_stop_event = asyncio.Event()
        self._lock = asyncio.Lock()

    async def _finalize_auto_state(self) -> None:
        current_task = asyncio.current_task()
        async with self._lock:
            self._is_auto_executing = False
            if current_task is not None and self._auto_task is current_task:
                self._auto_task = None

    def _generate_command_code(self) -> str:
        self._counter += 1
        return f"CMD-{datetime.now().strftime('%Y%m%d%H%M%S')}-{self._counter:03d}"

    def _resolve_location(
        self,
        *,
        location_type: str | None,
        location_id: str | None,
        default_location: DeviceLocation,
        field_name: str,
    ) -> DeviceLocation:
        if location_type and location_type != default_location.location_type:
            raise HTTPException(status_code=400, detail=f"不支持的{field_name}位置类型: {location_type}")
        if location_id and location_id != default_location.location_id:
            raise HTTPException(status_code=400, detail=f"无效的{field_name}位置: {location_id}")
        return default_location

    def _resolve_command_locations_from_params(self, params: JsonDict) -> tuple[DeviceLocation, DeviceLocation]:
        source_payload = cast("JsonDict", params.get("source")) if isinstance(params.get("source"), dict) else {}
        target_payload = cast("JsonDict", params.get("target")) if isinstance(params.get("target"), dict) else {}
        source = self._resolve_location(
            location_type=cast("str | None", source_payload.get("location_type") or params.get("source_type")),
            location_id=cast("str | None", source_payload.get("location_id") or params.get("source_loc")),
            default_location=cast("DeviceLocation", DEVICE_INFO["source"]),
            field_name="源",
        )
        target = self._resolve_location(
            location_type=cast("str | None", target_payload.get("location_type") or params.get("target_type")),
            location_id=cast("str | None", target_payload.get("location_id") or params.get("target_loc")),
            default_location=cast("DeviceLocation", DEVICE_INFO["target"]),
            field_name="目标",
        )
        return source, target

    async def _callback_result_to_wes(
        self,
        *,
        command_code: str,
        result: str,
        source: DeviceLocation,
        target: DeviceLocation,
        error_detail: JsonDict | None,
    ) -> JsonDict:
        payload: JsonDict = {
            "command_code": command_code,
            "device_code": self.device_code,
            "result": result,
            "finish_time": current_millis(),
            "data": {
                "actual_qty": 1,
                "location": target.location_id,
                "actual_source": source.location_id,
                "actual_target": target.location_id,
                "pick_and_put_result": "MOVE_FAILED" if result == "FAILED" else "MOVE_FINISHED",
            },
        }
        if error_detail is not None:
            payload["error_detail"] = error_detail
        logger.info(f"回调结果到 WES: device={self.device_code}, command={command_code}, result={result}")
        return await post_signed_json(WES_RESULT_CALLBACK_URL, payload)

    async def execute_command(
        self,
        *,
        task_type: str = "MOVE_FORWARD",
        source_type: str | None = "PIPELINE_PLATFORM",
        target_type: str | None = "PIPELINE_PLATFORM",
        source_location_id: str | None = None,
        target_location_id: str | None = None,
        simulate_failure: bool = False,
        execution_time: float = EXECUTION_TIME,
        command_code: str | None = None,
        report_result: bool = True,
    ) -> ExecutionRecord:
        if task_type != "MOVE_FORWARD":
            raise HTTPException(status_code=400, detail="流水线仅支持 MOVE_FORWARD")
        source = self._resolve_location(
            location_type=source_type,
            location_id=source_location_id,
            default_location=cast("DeviceLocation", DEVICE_INFO["source"]),
            field_name="源",
        )
        target = self._resolve_location(
            location_type=target_type,
            location_id=target_location_id,
            default_location=cast("DeviceLocation", DEVICE_INFO["target"]),
            field_name="目标",
        )
        resolved_command_code = command_code or self._generate_command_code()
        async with self._lock:
            started_at = datetime.now()
            await asyncio.sleep(execution_time)
            result = "FAILED" if simulate_failure else "SUCCESS"
            error_detail = (
                {"error_code": "2002", "error_message": "流水线传输失败"} if simulate_failure else None
            )
            if report_result:
                try:
                    await self._callback_result_to_wes(
                        command_code=resolved_command_code,
                        result=result,
                        source=source,
                        target=target,
                        error_detail=error_detail,
                    )
                except Exception as exc:
                    logger.error(f"WES 结果回调失败: {exc}")
            finished_at = datetime.now()
            duration_ms = int((finished_at - started_at).total_seconds() * 1000)
            record = ExecutionRecord(
                execution_id=f"EXEC-{datetime.now().strftime('%Y%m%d%H%M%S')}-{self._execution_count:03d}",
                command_code=resolved_command_code,
                task_type=task_type,
                source=source.to_payload(),
                target=target.to_payload(),
                result=result,
                message="流水线传输成功" if result == "SUCCESS" else "流水线传输失败",
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
            )
            self._executions.append(record)
            self._execution_count += 1
            if result == "SUCCESS":
                self._success_count += 1
            else:
                self._failure_count += 1
                DEVICE_STATUS["error_code"] = "2002"
            return record

    async def execute_wes_command(self, payload: DeviceCommandPayload) -> ExecutionRecord:
        source, target = self._resolve_command_locations_from_params(payload.params or {})
        return await self.execute_command(
            task_type=payload.task_type,
            source_type=source.location_type,
            target_type=target.location_type,
            source_location_id=source.location_id,
            target_location_id=target.location_id,
            simulate_failure=bool((payload.params or {}).get("simulate_failure", False)),
            execution_time=float((payload.params or {}).get("execution_time", EXECUTION_TIME)),
            command_code=payload.command_code,
            report_result=True,
        )

    async def _auto_execute_loop(self, interval_seconds: int, max_executions: int | None) -> None:
        executed = 0
        try:
            while not self._auto_stop_event.is_set():
                if max_executions is not None and executed >= max_executions:
                    break
                try:
                    await self.execute_command(report_result=False)
                    executed += 1
                except Exception as exc:
                    logger.error(f"自动执行失败: {exc}")
                try:
                    await asyncio.wait_for(self._auto_stop_event.wait(), timeout=interval_seconds)
                    break
                except TimeoutError:  # noqa: S112
                    continue
        finally:
            await self._finalize_auto_state()

    async def start_auto_execution(self, config: AutoExecuteConfig) -> AutoExecuteStartResponse:
        async with self._lock:
            if self._is_auto_executing:
                raise HTTPException(status_code=400, detail="自动执行已在运行中")
            self._auto_stop_event.clear()
            self._auto_task = asyncio.create_task(
                self._auto_execute_loop(config.interval_seconds, config.max_executions)
            )
            self._is_auto_executing = True
        return AutoExecuteStartResponse(
            status="started",
            device_code=self.device_code,
            interval_seconds=config.interval_seconds,
            max_executions=config.max_executions,
        )

    async def stop_auto_execution(self) -> AutoExecuteStopResponse:
        current_task = asyncio.current_task()
        async with self._lock:
            if not self._is_auto_executing:
                return AutoExecuteStopResponse(
                    status="stopped",
                    device_code=self.device_code,
                    execution_count=self._execution_count,
                )
            self._auto_stop_event.set()
            auto_task = self._auto_task
            self._is_auto_executing = False
            self._auto_task = None
        if auto_task and auto_task is not current_task:
            auto_task.cancel()
            try:
                await auto_task
            except asyncio.CancelledError:
                pass
        return AutoExecuteStopResponse(
            status="stopped",
            device_code=self.device_code,
            execution_count=self._execution_count,
        )

    def get_status(self, current_command: JsonDict | None = None) -> PipelineStatusResponse:
        return PipelineStatusResponse(
            device_code=self.device_code,
            status=DEVICE_STATUS["status"],
            current_command_code=current_command["command_code"] if current_command else None,
            error_code=DEVICE_STATUS["error_code"],
            is_auto_executing=self._is_auto_executing,
            execution_count=self._execution_count,
            success_count=self._success_count,
            failure_count=self._failure_count,
            timestamp=current_millis(),
        )

    def get_executions(self, limit: int = 50) -> list[ExecutionRecord]:
        return self._executions[-limit:]


pipeline_simulator = PipelineSimulator()
current_command: JsonDict | None = None
current_command_task: asyncio.Task[None] | None = None

app = FastAPI(
    title="SMT 粗分机流水线 Mock 服务",
    description="模拟 SMT 粗分机单线流水线设备",
    version="2.0.0",
)


@app.get("/api/v1/device/status", response_model=DeviceStatusResponse)
async def get_status() -> DeviceStatusResponse:
    status = pipeline_simulator.get_status(current_command=current_command)
    return DeviceStatusResponse(
        device_code=status.device_code,
        status=cast("Literal['IDLE', 'RUNNING', 'ERROR', 'OFFLINE']", status.status),
        current_command_code=status.current_command_code,
        error_code=status.error_code,
        timestamp=status.timestamp,
    )


@app.post("/api/v1/device/command", response_model=DeviceCommandAck)
async def receive_command(payload: DeviceCommandPayload) -> DeviceCommandAck:
    global current_command, current_command_task
    logger.info(f"[{DEVICE_INFO['device_name']}] 收到指令: {payload.command_code}, task_type={payload.task_type}")
    if DEVICE_STATUS["status"] == "RUNNING":
        raise HTTPException(status_code=503, detail="Device Busy")
    if payload.task_type != "MOVE_FORWARD":
        raise HTTPException(status_code=400, detail="流水线仅支持 MOVE_FORWARD")
    DEVICE_STATUS["status"] = "RUNNING"
    DEVICE_STATUS["error_code"] = "NONE"
    current_command = cast("JsonDict", payload.model_dump())
    current_command_task = asyncio.create_task(_execute_wes_command_with_cleanup(payload))
    return DeviceCommandAck(code=200, message="Accepted", trace_id=f"{DEVICE_CODE}-LOG-{payload.command_code}")


async def _execute_wes_command_with_cleanup(payload: DeviceCommandPayload) -> None:
    global current_command, current_command_task
    try:
        await pipeline_simulator.execute_wes_command(payload)
    except asyncio.CancelledError:
        logger.info(f"[{DEVICE_INFO['device_name']}] 指令已中断: {payload.command_code}")
        raise
    finally:
        DEVICE_STATUS["status"] = "IDLE"
        current_command = None
        current_command_task = None


@app.post("/api/v1/device/cancel", response_model=DeviceCommandAck)
async def cancel_command(request: CancelRequest) -> DeviceCommandAck:
    global current_command, current_command_task
    logger.info(f"[{DEVICE_INFO['device_name']}] 收到取消指令请求: {request.command_code}")
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
async def debug_execute(request: ManualExecuteRequest) -> ExecutionRecord:
    return await pipeline_simulator.execute_command(
        task_type=request.task_type,
        source_type=request.source_type,
        target_type=request.target_type,
        source_location_id=request.source_location_id,
        target_location_id=request.target_location_id,
        simulate_failure=request.simulate_failure,
        execution_time=request.execution_time,
        command_code=request.command_code,
        report_result=request.report_result,
    )


@app.post("/debug/auto/start", response_model=AutoExecuteStartResponse)
async def start_auto_execute(config: AutoExecuteConfig) -> AutoExecuteStartResponse:
    return await pipeline_simulator.start_auto_execution(config)


@app.post("/debug/auto/stop", response_model=AutoExecuteStopResponse)
async def stop_auto_execute() -> AutoExecuteStopResponse:
    return await pipeline_simulator.stop_auto_execution()


@app.get("/debug/executions", response_model=list[ExecutionRecord])
async def get_pipeline_executions(limit: int = 50) -> list[ExecutionRecord]:
    return pipeline_simulator.get_executions(limit)


@app.get("/", response_model=PipelineRootResponse)
async def root() -> PipelineRootResponse:
    return PipelineRootResponse(
        service="SMT 粗分机流水线 Mock 服务",
        version="2.0.0",
        status="running",
        device_code=DEVICE_INFO["device_code"],
        device_name=DEVICE_INFO["device_name"],
        description=DEVICE_INFO["description"],
        task_types=cast("list[str]", DEVICE_INFO["task_types"]),
        device_status=pipeline_simulator.get_status(current_command=current_command),
        wes_result_callback_url=WES_RESULT_CALLBACK_URL,
    )


class PipelineMockServer:
    def __init__(self, host: str = "127.0.0.1", port: int = cast("int", DEVICE_INFO["port"])):
        self.host = host
        self.port = port
        self._server: Server | None = None
        self.config = Config(app=app, host=host, port=port, log_level="info")

    async def start(self) -> None:
        logger.info(f"SMT 粗分机流水线 Mock 服务启动: http://{self.host}:{self.port}")
        logger.info(f"设备编码: {DEVICE_INFO['device_code']}")
        logger.info(f"结果回调地址: {WES_RESULT_CALLBACK_URL}")
        logger.info("正式接口:")
        logger.info("  - GET  /api/v1/device/status")
        logger.info("  - POST /api/v1/device/command")
        logger.info("  - POST /api/v1/device/cancel")
        logger.info("调试接口:")
        logger.info("  - POST /debug/execute")
        logger.info("  - POST /debug/auto/start")
        logger.info("  - POST /debug/auto/stop")
        logger.info("  - GET  /debug/executions")
        self._server = Server(self.config)
        await self._server.serve()

    def run(self) -> None:
        asyncio.run(self.start())


if __name__ == "__main__":
    PipelineMockServer().run()
