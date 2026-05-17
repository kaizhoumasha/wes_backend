"""
SMT 粗分机 WMS/RCS 新货架补充 Mock 服务

正式接口:
- POST /api/rack-exchange

调试接口:
- POST /debug/execute
- POST /debug/mode
- GET  /debug/requests
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field
from uvicorn import Config, Server

project_root = Path(__file__).parent.parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from tests.mock.smt_classifier.mock_support import (
    WES_EXTERNAL_CALLBACK_URL,
    JsonDict,
    current_millis,
    post_signed_json,
    register_mock_exception_handlers,
)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

DEFAULT_PORT = int(os.getenv("RACK_EXCHANGE_MOCK_PORT", "8010"))
DEFAULT_MODE = os.getenv("RACK_EXCHANGE_MODE", "success")
EXECUTION_TIME = float(os.getenv("RACK_EXCHANGE_EXECUTION_TIME", "0.5"))
SOURCE_SYSTEM = os.getenv("RACK_EXCHANGE_SOURCE_SYSTEM", "WMS")
RACK_ID = os.getenv("RACK_EXCHANGE_RACK_ID", "NHW-1CLJ-0096")
RACK_SLOT_CODE = os.getenv("RACK_EXCHANGE_RACK_SLOT_CODE", "C")
RACK_SLOT_SIDE = "1" if RACK_SLOT_CODE in {"C", "D"} else "0"
RACK_SLOT_LOCATION_CODE = os.getenv(
    "RACK_EXCHANGE_RACK_SLOT_LOCATION_CODE",
    f"{RACK_ID}-1{RACK_SLOT_CODE}-{RACK_SLOT_SIDE}",
)
BIN_ID = os.getenv("RACK_EXCHANGE_BIN_ID", "BIN-MOCK-001")
BIN_ORIENTATION_CODE = os.getenv("RACK_EXCHANGE_BIN_ORIENTATION_CODE", f"{BIN_ID}-A")
BIN_TYPE = os.getenv("RACK_EXCHANGE_BIN_TYPE", "6格箱")
BIN_CELL_LOCATION = os.getenv("RACK_EXCHANGE_BIN_CELL_LOCATION", f"{BIN_ID}-4")
CELL_TYPE = os.getenv("RACK_EXCHANGE_CELL_TYPE", "SEVEN_INCH")
REMAINING_DEPTH = os.getenv("RACK_EXCHANGE_REMAINING_DEPTH", "300")


class RackExchangeRequest(BaseModel):
    request_type: str = "SMT_RACK_SUPPLY"
    dispatch_key: str
    trace_id: str | None = None
    material: JsonDict = Field(default_factory=dict)
    current_rack_snapshot: JsonDict = Field(default_factory=dict)
    actions: list[str] = Field(default_factory=list)
    resume_callback_type: str = "WMS_RACK_ARRIVED"
    reason_code: str | None = None


class RackExchangeResponse(BaseModel):
    code: int
    message: str
    data: JsonDict = Field(default_factory=dict)


class RackExchangeDebugModeRequest(BaseModel):
    mode: Literal["success", "progress", "failed", "timeout"]


class RackExchangeRecord(BaseModel):
    request_id: str
    request_type: str
    dispatch_key: str
    trace_id: str | None
    callback_type: str | None = None
    result: str
    callback_url: str
    received_at: datetime
    finished_at: datetime | None = None


def _iso_utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class RackExchangeSimulator:
    def __init__(self, mode: str = DEFAULT_MODE):
        self.mode = mode
        self.records: list[RackExchangeRecord] = []

    async def execute_request(self, request: RackExchangeRequest) -> RackExchangeRecord:
        record = RackExchangeRecord(
            request_id=f"RACK-MOCK-{current_millis()}",
            request_type=request.request_type,
            dispatch_key=request.dispatch_key,
            trace_id=request.trace_id,
            result="PENDING",
            callback_url=WES_EXTERNAL_CALLBACK_URL,
            received_at=datetime.now(UTC),
        )
        self.records.append(record)

        if self.mode == "timeout":
            return record

        await asyncio.sleep(EXECUTION_TIME)

        if self.mode == "progress":
            callback_type = "WMS_RACK_EXCHANGE_PROGRESS"
            result = "IN_PROGRESS"
            payload = self._build_callback_payload(
                request=request,
                record=record,
                callback_type=callback_type,
                extra={"status": result, "message": "WMS/RCS mock rack supply in progress"},
            )
        elif self.mode == "failed":
            callback_type = "WMS_RACK_EXCHANGE_FAILED"
            result = "FAILED"
            payload = self._build_callback_payload(
                request=request,
                record=record,
                callback_type=callback_type,
                extra={
                    "reason_code": request.reason_code or "RCS_RACK_SUPPLY_FAILED",
                    "reason_message": "WMS/RCS mock rack supply failed",
                },
            )
        else:
            callback_type = request.resume_callback_type or "WMS_RACK_ARRIVED"
            result = "SUCCESS"
            payload = self._build_callback_payload(
                request=request,
                record=record,
                callback_type=callback_type,
                extra={"active_bin_rack": self._build_active_bin_rack()},
            )

        logger.info("WMS/RCS mock 回调 WES: dispatch_key=%s callback_type=%s", request.dispatch_key, callback_type)
        await post_signed_json(WES_EXTERNAL_CALLBACK_URL, payload)
        record.callback_type = callback_type
        record.result = result
        record.finished_at = datetime.now(UTC)
        return record

    def _build_callback_payload(
        self,
        *,
        request: RackExchangeRequest,
        record: RackExchangeRecord,
        callback_type: str,
        extra: JsonDict,
    ) -> JsonDict:
        now = _iso_utc_now()
        payload: JsonDict = {
            "callback_type": callback_type,
            "trace_id": request.trace_id,
            "dispatch_key": request.dispatch_key,
            "source_system": SOURCE_SYSTEM if SOURCE_SYSTEM in {"WMS", "RCS"} else "WMS",
            "source_event_id": f"{SOURCE_SYSTEM}-{record.request_id}",
            "source_version": "1",
            "occurred_at": now,
            "request_id": record.request_id,
            "timestamp": now,
            "signature": "mock-signature",
        }
        payload.update(extra)
        return payload

    def _build_active_bin_rack(self) -> JsonDict:
        return {
            "rack_id": RACK_ID,
            "rack_code": RACK_ID,
            "cells": [
                {
                    "rack_id": RACK_ID,
                    "rack_slot_code": RACK_SLOT_CODE,
                    "rack_slot_location_code": RACK_SLOT_LOCATION_CODE,
                    "bin_id": BIN_ID,
                    "bin_orientation_code": BIN_ORIENTATION_CODE,
                    "bin_type": BIN_TYPE,
                    "bin_cell_location": BIN_CELL_LOCATION,
                    "bin_cell_index": BIN_CELL_LOCATION.rsplit("-", 1)[-1],
                    "status": "EMPTY",
                    "cell_type": CELL_TYPE,
                    "remaining_depth": REMAINING_DEPTH,
                }
            ],
        }

    def reset(self) -> None:
        self.records.clear()


rack_exchange_simulator = RackExchangeSimulator()
_background_tasks: set[asyncio.Task[RackExchangeRecord]] = set()
app = FastAPI(
    title="SMT 粗分机 WMS/RCS 新货架补充 Mock 服务",
    description="模拟 WES 发起新货架补充请求后，WMS/RCS 回调 WMS_RACK_ARRIVED 等事件。",
    version="1.0.0",
)
register_mock_exception_handlers(app, logger, service_name="SMT_RACK_EXCHANGE_MOCK")


@app.post("/api/rack-exchange", response_model=RackExchangeResponse)
async def request_rack_exchange(request: RackExchangeRequest) -> RackExchangeResponse:
    logger.info(
        "收到 WMS/RCS 新货架补充请求: dispatch_key=%s mode=%s", request.dispatch_key, rack_exchange_simulator.mode
    )
    task = asyncio.create_task(rack_exchange_simulator.execute_request(request))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return RackExchangeResponse(
        code=200,
        message="ACCEPTED",
        data={
            "dispatch_key": request.dispatch_key,
            "mode": rack_exchange_simulator.mode,
            "callback_url": WES_EXTERNAL_CALLBACK_URL,
        },
    )


@app.post("/debug/execute", response_model=RackExchangeRecord)
async def debug_execute(request: RackExchangeRequest) -> RackExchangeRecord:
    return await rack_exchange_simulator.execute_request(request)


@app.post("/debug/mode")
async def debug_mode(request: RackExchangeDebugModeRequest) -> JsonDict:
    rack_exchange_simulator.mode = request.mode
    return {"status": "updated", "mode": rack_exchange_simulator.mode}


@app.post("/debug/reset")
async def debug_reset() -> JsonDict:
    rack_exchange_simulator.reset()
    return {"status": "reset"}


@app.get("/debug/requests", response_model=list[RackExchangeRecord])
async def debug_requests(limit: int = 50) -> list[RackExchangeRecord]:
    return rack_exchange_simulator.records[-limit:]


@app.get("/")
async def root() -> JsonDict:
    return {
        "service": "rack_exchange_mock",
        "mode": rack_exchange_simulator.mode,
        "status_url": "/",
        "request_url": "/api/rack-exchange",
        "callback_url": WES_EXTERNAL_CALLBACK_URL,
    }


class RackExchangeMockServer:
    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_PORT):
        self.host = host
        self.port = port
        self.config = Config(app=app, host=host, port=port, log_level="info")
        self._server: Server | None = None

    async def start(self) -> None:
        logger.info("SMT WMS/RCS 新货架补充 Mock 服务启动: http://%s:%s", self.host, self.port)
        logger.info("正式接口:")
        logger.info("  - POST /api/rack-exchange")
        logger.info("调试接口:")
        logger.info("  - POST /debug/execute")
        logger.info("  - POST /debug/mode")
        logger.info("  - POST /debug/reset")
        logger.info("  - GET  /debug/requests")
        self._server = Server(self.config)
        await self._server.serve()

    def run(self) -> None:
        asyncio.run(self.start())


if __name__ == "__main__":
    RackExchangeMockServer().run()
