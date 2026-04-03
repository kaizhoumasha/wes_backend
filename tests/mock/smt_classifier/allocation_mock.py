"""
SMT 粗分机库位分配 Mock 服务

正式接口:
- POST /api/v1/bin-allocation/allocate
- GET  /api/v1/bin-allocation/status

调试接口:
- POST /debug/reset
- POST /debug/mode
- GET  /debug/requests
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field
from uvicorn import Config, Server

project_root = Path(__file__).parent.parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from tests.mock.smt_classifier.mock_support import JsonDict, current_millis  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

DEFAULT_PORT = int(os.getenv("ALLOCATION_MOCK_PORT", "8008"))
DEFAULT_MODE = os.getenv("ALLOCATION_MODE", "agv_required_then_allocated")


class AllocationRequest(BaseModel):
    request_code: str
    workline_code: str
    business_key: str
    barcode: str
    reel_diameter: str | None = None
    reel_thickness: str | None = None
    inspection_result: str | None = None
    source_location: str | None = None
    timestamp: int


class AllocationResponse(BaseModel):
    code: int
    message: str
    data: JsonDict = Field(default_factory=dict)


class AllocationStatusResponse(BaseModel):
    service: str
    mode: str
    total_requests: int
    business_keys: list[str]
    timestamp: int


class AllocationDebugModeRequest(BaseModel):
    mode: Literal["allocated", "agv_required_then_allocated", "agv_required", "fail", "timeout"]


class AllocationRecord(BaseModel):
    request_code: str
    business_key: str
    allocation_status: str
    request_count: int
    received_at: datetime


class AllocationSimulator:
    def __init__(self, mode: str = DEFAULT_MODE):
        self.mode = mode
        self.request_count_by_business_key: dict[str, int] = {}
        self.records: list[AllocationRecord] = []

    async def allocate(self, request: AllocationRequest) -> AllocationResponse:
        if self.mode == "timeout":
            await asyncio.sleep(15)

        business_count = self.request_count_by_business_key.get(request.business_key, 0) + 1
        self.request_count_by_business_key[request.business_key] = business_count

        if self.mode == "fail":
            allocation_status = "FAILED"
            response = AllocationResponse(code=500, message="FAILED", data={"allocation_status": allocation_status})
        elif self.mode == "allocated":
            allocation_status = "ALLOCATED"
            response = AllocationResponse(
                code=200,
                message="ALLOCATED",
                data={
                    "allocation_status": allocation_status,
                    "target_bin": self._build_target_bin(request, business_count),
                },
            )
        elif self.mode == "agv_required" or business_count == 1:
            allocation_status = "AGV_REQUIRED"
            response = AllocationResponse(
                code=200,
                message="AGV_REQUIRED",
                data={
                    "allocation_status": allocation_status,
                    "agv_request": self._build_agv_request(request, business_count),
                },
            )
        else:
            allocation_status = "ALLOCATED"
            response = AllocationResponse(
                code=200,
                message="ALLOCATED",
                data={
                    "allocation_status": allocation_status,
                    "target_bin": self._build_target_bin(request, business_count),
                },
            )

        self.records.append(
            AllocationRecord(
                request_code=request.request_code,
                business_key=request.business_key,
                allocation_status=allocation_status,
                request_count=business_count,
                received_at=datetime.now(),
            )
        )
        return response

    def _build_target_bin(self, request: AllocationRequest, request_count: int) -> JsonDict:
        return {
            "station_location_id": "STATION_OUTPUT1",
            "rack_id": f"RACK_{request_count:03d}",
            "bin_id": f"BIN_{100 + request_count}",
            "bin_type": "三格箱",
            "bin_cell_location": str(request_count),
            "reel_layer": "15",
            "reel_thickness": request.reel_thickness or "20",
            "reel_diameter": request.reel_diameter or "15inch",
            "reel_totalthickness": "300",
        }

    def _build_agv_request(self, request: AllocationRequest, request_count: int) -> JsonDict:
        return {
            "request_code": f"AGV-{request.business_key}-{request_count:02d}",
            "from_location": "RACK_BUFFER_A",
            "to_location": "STATION_OUTPUT1",
            "rack_type": "SMT_BIN_RACK",
            "reason": "NO_AVAILABLE_BIN",
            "workline_code": request.workline_code,
        }

    def reset(self) -> None:
        self.request_count_by_business_key.clear()
        self.records.clear()


allocation_simulator = AllocationSimulator()
app = FastAPI(
    title="SMT 粗分机库位分配 Mock 服务",
    description="模拟库位分配正式接口与调试接口",
    version="1.0.0",
)


@app.post("/api/v1/bin-allocation/allocate", response_model=AllocationResponse)
async def allocate_bin(request: AllocationRequest) -> AllocationResponse:
    logger.info("收到库位分配请求: %s business_key=%s", request.request_code, request.business_key)
    return await allocation_simulator.allocate(request)


@app.get("/api/v1/bin-allocation/status", response_model=AllocationStatusResponse)
async def get_status() -> AllocationStatusResponse:
    return AllocationStatusResponse(
        service="allocation_mock",
        mode=allocation_simulator.mode,
        total_requests=len(allocation_simulator.records),
        business_keys=sorted(allocation_simulator.request_count_by_business_key.keys()),
        timestamp=current_millis(),
    )


@app.post("/debug/reset")
async def debug_reset() -> JsonDict:
    allocation_simulator.reset()
    return {"status": "reset"}


@app.post("/debug/mode")
async def debug_change_mode(request: AllocationDebugModeRequest) -> JsonDict:
    allocation_simulator.mode = request.mode
    return {"status": "updated", "mode": allocation_simulator.mode}


@app.get("/debug/requests", response_model=list[AllocationRecord])
async def debug_requests(limit: int = 50) -> list[AllocationRecord]:
    return allocation_simulator.records[-limit:]


@app.get("/")
async def root() -> JsonDict:
    return {
        "service": "allocation_mock",
        "mode": allocation_simulator.mode,
        "status_url": "/api/v1/bin-allocation/status",
        "allocate_url": "/api/v1/bin-allocation/allocate",
    }


class AllocationMockServer:
    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_PORT):
        self.host = host
        self.port = port
        self.config = Config(app=app, host=host, port=port, log_level="info")
        self._server: Server | None = None

    async def start(self) -> None:
        logger.info("SMT 库位分配 Mock 服务启动: http://%s:%s", self.host, self.port)
        logger.info("正式接口:")
        logger.info("  - POST /api/v1/bin-allocation/allocate")
        logger.info("  - GET  /api/v1/bin-allocation/status")
        logger.info("调试接口:")
        logger.info("  - POST /debug/reset")
        logger.info("  - POST /debug/mode")
        logger.info("  - GET  /debug/requests")
        self._server = Server(self.config)
        await self._server.serve()

    def run(self) -> None:
        asyncio.run(self.start())


if __name__ == "__main__":
    AllocationMockServer().run()
