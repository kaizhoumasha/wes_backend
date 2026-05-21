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
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field, field_validator
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
DEFAULT_RACK_EXCHANGE_PROFILE = "seven_inch_six_cell"
SINGLE_CELL_OVERRIDE_ENV_NAMES = (
    "RACK_EXCHANGE_PROFILE",
    "RACK_EXCHANGE_RACK_SLOT_CODE",
    "RACK_EXCHANGE_RACK_SLOT_LOCATION_CODE",
    "RACK_EXCHANGE_BIN_ID",
    "RACK_EXCHANGE_BIN_ORIENTATION_CODE",
    "RACK_EXCHANGE_BIN_TYPE",
    "RACK_EXCHANGE_BIN_CELL_LOCATION",
    "RACK_EXCHANGE_CELL_TYPE",
)
RACK_EXCHANGE_PROFILE_ALIASES = {
    "seven_inch_six_cell": "seven_inch_six_cell",
    "7inch_6cell": "seven_inch_six_cell",
    "7_inch_6_cell": "seven_inch_six_cell",
    "six_cell": "seven_inch_six_cell",
    "6_cell": "seven_inch_six_cell",
    "6格箱": "seven_inch_six_cell",
    "large_three_cell": "large_three_cell",
    "15inch_3cell": "large_three_cell",
    "15_inch_3_cell": "large_three_cell",
    "three_cell_large": "large_three_cell",
    "3_cell": "large_three_cell",
    "3格箱": "large_three_cell",
}
RACK_EXCHANGE_PROFILES = {
    "seven_inch_six_cell": {
        "bin_type": "6格箱",
        "bin_cell_index": "4",
        "cell_type": "SEVEN_INCH",
    },
    "large_three_cell": {
        "bin_type": "3格箱",
        "bin_cell_index": "7",
        "cell_type": "LARGE",
    },
}
DEFAULT_MIXED_RACK_BINS = (
    {
        "rack_slot_code": "A",
        "bin_id": "BIN-MOCK-6A",
        "bin_type": "6格箱",
        "cell_indexes": ("1", "2", "3", "4", "5", "6"),
    },
    {
        "rack_slot_code": "B",
        "bin_id": "BIN-MOCK-6B",
        "bin_type": "6格箱",
        "cell_indexes": ("1", "2", "3", "4", "5", "6"),
    },
    {"rack_slot_code": "C", "bin_id": "BIN-MOCK-3C", "bin_type": "3格箱", "cell_indexes": ("1", "2", "7")},
    {"rack_slot_code": "D", "bin_id": "BIN-MOCK-3D", "bin_type": "3格箱", "cell_indexes": ("1", "2", "7")},
)


@dataclass(frozen=True)
class RackCellConfig:
    profile: str
    rack_id: str
    rack_slot_code: str
    rack_slot_location_code: str
    bin_id: str
    bin_orientation_code: str
    bin_type: str
    bin_cell_location: str
    cell_type: str
    remaining_depth: str


def _canonical_rack_exchange_profile(profile: str | None = None) -> str:
    raw_profile = profile or os.getenv("RACK_EXCHANGE_PROFILE") or DEFAULT_RACK_EXCHANGE_PROFILE
    normalized = raw_profile.strip().lower().replace("-", "_")
    canonical = RACK_EXCHANGE_PROFILE_ALIASES.get(normalized)
    if canonical is None:
        supported = ", ".join(sorted(RACK_EXCHANGE_PROFILES))
        raise ValueError(f"无效的 RACK_EXCHANGE_PROFILE={raw_profile!r}，支持: {supported}")
    return canonical


def _single_cell_override_requested(profile: str | None = None) -> bool:
    return bool(profile) or any(os.getenv(env_name) for env_name in SINGLE_CELL_OVERRIDE_ENV_NAMES)


def _rack_slot_location_code(rack_id: str, rack_slot_code: str) -> str:
    rack_slot_side = "1" if rack_slot_code in {"C", "D"} else "0"
    return f"{rack_id}-1{rack_slot_code}-{rack_slot_side}"


def _build_rack_cell_config(profile: str | None = None) -> RackCellConfig:
    profile_name = _canonical_rack_exchange_profile(profile)
    profile_defaults = RACK_EXCHANGE_PROFILES[profile_name]
    rack_id = os.getenv("RACK_EXCHANGE_RACK_ID", RACK_ID)
    rack_slot_code = os.getenv("RACK_EXCHANGE_RACK_SLOT_CODE", RACK_SLOT_CODE)
    bin_id = os.getenv("RACK_EXCHANGE_BIN_ID", BIN_ID)
    return RackCellConfig(
        profile=profile_name,
        rack_id=rack_id,
        rack_slot_code=rack_slot_code,
        rack_slot_location_code=os.getenv(
            "RACK_EXCHANGE_RACK_SLOT_LOCATION_CODE",
            _rack_slot_location_code(rack_id, rack_slot_code),
        ),
        bin_id=bin_id,
        bin_orientation_code=os.getenv("RACK_EXCHANGE_BIN_ORIENTATION_CODE", f"{bin_id}-A"),
        bin_type=os.getenv("RACK_EXCHANGE_BIN_TYPE", profile_defaults["bin_type"]),
        bin_cell_location=os.getenv(
            "RACK_EXCHANGE_BIN_CELL_LOCATION",
            f"{bin_id}-{profile_defaults['bin_cell_index']}",
        ),
        cell_type=os.getenv("RACK_EXCHANGE_CELL_TYPE", profile_defaults["cell_type"]),
        remaining_depth=os.getenv("RACK_EXCHANGE_REMAINING_DEPTH", REMAINING_DEPTH),
    )


def _build_mixed_rack_cells(rack_id: str) -> list[JsonDict]:
    cells: list[JsonDict] = []
    remaining_depth = os.getenv("RACK_EXCHANGE_REMAINING_DEPTH", REMAINING_DEPTH)
    for bin_config in DEFAULT_MIXED_RACK_BINS:
        rack_slot_code = str(bin_config["rack_slot_code"])
        bin_id = str(bin_config["bin_id"])
        bin_type = str(bin_config["bin_type"])
        for cell_index in bin_config["cell_indexes"]:
            cell_type = "LARGE" if bin_type == "3格箱" and cell_index == "7" else "SEVEN_INCH"
            cells.append(
                {
                    "rack_id": rack_id,
                    "rack_slot_code": rack_slot_code,
                    "rack_slot_location_code": _rack_slot_location_code(rack_id, rack_slot_code),
                    "bin_id": bin_id,
                    "bin_orientation_code": f"{bin_id}-A",
                    "bin_type": bin_type,
                    "bin_cell_location": f"{bin_id}-{cell_index}",
                    "bin_cell_index": cell_index,
                    "status": "EMPTY",
                    "cell_type": cell_type,
                    "remaining_depth": remaining_depth,
                }
            )
    return cells


class RackExchangeAction(BaseModel):
    action: str
    required: bool = True


class RackExchangeRequest(BaseModel):
    request_type: str = "SMT_RACK_OPERATION"
    dispatch_key: str
    trace_id: str | None = None
    material: JsonDict = Field(default_factory=dict)
    current_rack_snapshot: JsonDict = Field(default_factory=dict)
    actions: list[RackExchangeAction] = Field(default_factory=list)
    resume_callback_type: str = "WMS_RACK_ARRIVED"
    reason_code: str | None = None

    @field_validator("actions", mode="before")
    @classmethod
    def _normalize_actions(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, dict):
            return [value]
        return value


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
    def __init__(self, mode: str = DEFAULT_MODE, profile: str | None = None):
        self.mode = mode
        self.cell_config = _build_rack_cell_config(profile) if _single_cell_override_requested(profile) else None
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
                    "reason_code": request.reason_code or "RCS_RACK_OPERATION_FAILED",
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
        cell_config = self.cell_config
        if cell_config is None:
            rack_id = os.getenv("RACK_EXCHANGE_RACK_ID", RACK_ID)
            return {
                "rack_id": rack_id,
                "rack_code": rack_id,
                "cells": _build_mixed_rack_cells(rack_id),
            }
        return {
            "rack_id": cell_config.rack_id,
            "rack_code": cell_config.rack_id,
            "cells": [
                {
                    "rack_id": cell_config.rack_id,
                    "rack_slot_code": cell_config.rack_slot_code,
                    "rack_slot_location_code": cell_config.rack_slot_location_code,
                    "bin_id": cell_config.bin_id,
                    "bin_orientation_code": cell_config.bin_orientation_code,
                    "bin_type": cell_config.bin_type,
                    "bin_cell_location": cell_config.bin_cell_location,
                    "bin_cell_index": cell_config.bin_cell_location.rsplit("-", 1)[-1],
                    "status": "EMPTY",
                    "cell_type": cell_config.cell_type,
                    "remaining_depth": cell_config.remaining_depth,
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
