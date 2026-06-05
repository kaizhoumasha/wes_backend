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
import importlib.util
import logging
import os
import re
import sys
from dataclasses import dataclass, field

# 添加项目根目录到 sys.path
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import BackgroundTasks, FastAPI, Request, Response
from pydantic import BaseModel
from uvicorn import Config, Server

project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))


def _load_sandbox_catalog() -> Any:
    """按文件加载共享 catalog，避免导入完整后端运行时包。"""

    catalog_path = project_root / "src" / "workline_runtime" / "sandbox_catalog.py"
    spec = importlib.util.spec_from_file_location("wes_mock_sandbox_catalog", catalog_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 WMS mock catalog: {catalog_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_sandbox_catalog = _load_sandbox_catalog()
mock_wms_inventory_seed = _sandbox_catalog.mock_wms_inventory_seed
mock_wms_materials_seed = _sandbox_catalog.mock_wms_materials_seed

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ============================================
# 种子数据 (Seed Data)
# ============================================

MOCK_MATERIALS = mock_wms_materials_seed()

MOCK_ZONES = [
    {
        "zone_code": "KITTING_AREA",
        "zone_name": "装箱区",
        "zone_type": "KITTING",
        "status": "ACTIVE",
        "allowed_rack_types": ["SINGLE_LAYER"],
        "max_concurrent_tasks": 10,
    },
    {
        "zone_code": "SMT_STORAGE",
        "zone_name": "SMT 自动化立库区",
        "zone_type": "STORAGE",
        "status": "ACTIVE",
        "allowed_rack_types": ["FIVE_LAYER"],
        "max_concurrent_tasks": 50,
    },
]

MOCK_LOCATIONS = [
    {
        "location_code": "KITTING_AREA_LOC_01",
        "zone_code": "KITTING_AREA",
        "location_type": "BUFFER",
        "status": "AVAILABLE",
    }
]

# 兼容遗留静态引用；运行时货架状态真源为 MockWmsState.rack_pool。
MOCK_RACKS = {
    "RACK-001": {
        "rack_id": "RACK-001",
        "rack_type": "SINGLE_LAYER",
        "status": "AVAILABLE",
        "current_location": "KITTING_AREA_LOC_01",
    },
    "RACK-3CELL-001": {
        "rack_id": "RACK-3CELL-001",
        "rack_type": "SINGLE_LAYER",
        "status": "AVAILABLE",
        "current_location": "KITTING_AREA_LOC_01",
    },
}

MOCK_INVENTORY = mock_wms_inventory_seed()
WES_EXTERNAL_CALLBACK_URL = os.getenv(
    "WES_EXTERNAL_CALLBACK_URL",
    "http://localhost:8001/api/v1/callback/external",
)
RACK_STATUS_CALLBACK_TYPES = {
    "WMS_RACK_TASK_RESULT",
    "RCS_RACK_TASK_RESULT",
    "WMS_RACK_TASK_PROGRESS",
    "RCS_RACK_TASK_PROGRESS",
    "WMS_RACK_EXCHANGE_PROGRESS",
    "RCS_RACK_EXCHANGE_PROGRESS",
}
RACK_STATUS_FIELDS = ("task_status", "status", "result", "external_status")
SEVEN_INCH_BIN_CELL_INDEXES = ("1", "2", "3", "4", "5", "6")
THREE_CELL_BIN_CELL_INDEXES = ("1", "2", "7")
THREE_CELL_LARGE_BIN_CELL_INDEX = "7"
RECENT_OPERATION_LIMIT = 100
RACK_SLOT_CODES = ("A", "B", "C", "D")
RACK_PHYSICAL_LAYOUTS = {
    "RACK-001": {
        "bin_type": "6格箱",
        "bin_prefix": "BIN",
        "cell_indexes": SEVEN_INCH_BIN_CELL_INDEXES,
        "layout_code": "SIX_CELL",
    },
    "RACK-3CELL-001": {
        "bin_type": "3格箱",
        "bin_prefix": "BIN-3CELL-001",
        "cell_indexes": THREE_CELL_BIN_CELL_INDEXES,
        "layout_code": "THREE_CELL",
    },
}

MOCK_GRNS = {
    "GRN.0001": {
        "grn_id": "GRN.0001",
        "po_number": "PO-2025-001",
        "po_item": "001",
        "status": "PARTIAL_RECEIVED",
        "dock_location": "DOCK-01",
        "arrival_date": "2026-03-14",
        "allow_mixed_pallet": True,
        "items": [
            {
                "material_id": "CAP001",
                "ordered_qty": 50000,
                "received_qty": 25000,
                "remaining_qty": 25000,
                "unit": "PCS",
            }
        ],
    }
}

# 故障注入状态
fault_injection_state = {"next_status": 200, "next_delay": 0.0}


def _known_rack_layout(rack_id: str) -> dict[str, Any] | None:
    if rack_id in RACK_PHYSICAL_LAYOUTS:
        return dict(RACK_PHYSICAL_LAYOUTS[rack_id])
    if rack_id.startswith(("RACK-6CELL-", "RACK-3CELL-")):
        return _rack_layout_from_pattern(rack_id)
    return None


def _rack_layout_from_pattern(rack_id: str) -> dict[str, Any]:
    if rack_id.startswith("RACK-3CELL-"):
        return {
            "bin_type": "3格箱",
            "bin_prefix": rack_id.replace("RACK-", "BIN-"),
            "cell_indexes": THREE_CELL_BIN_CELL_INDEXES,
            "layout_code": "THREE_CELL",
        }
    return {
        "bin_type": "6格箱",
        "bin_prefix": rack_id.replace("RACK-", "BIN-"),
        "cell_indexes": SEVEN_INCH_BIN_CELL_INDEXES,
        "layout_code": "SIX_CELL",
    }


def _build_rack_state(rack_id: str) -> dict[str, Any]:
    layout = _rack_layout_from_pattern(rack_id)
    return {
        "rack_id": rack_id,
        "rack_type": "SINGLE_LAYER",
        "status": "AVAILABLE",
        "current_location": "KITTING_AREA_LOC_01",
        "layout_code": layout["layout_code"],
        "bin_type": layout["bin_type"],
        "active_position_code": None,
        "allocated_operation_key": None,
    }


def build_initial_rack_pool() -> dict[str, dict[str, Any]]:
    """构造有限货架池初始状态；物理布局常量仍保留为 builder 输入。"""

    rack_ids = ["RACK-001", *(f"RACK-6CELL-{index:03d}" for index in range(1, 7))]
    rack_ids.extend(f"RACK-3CELL-{index:03d}" for index in range(1, 5))
    return {rack_id: _build_rack_state(rack_id) for rack_id in rack_ids}


@dataclass
class MockWmsState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    rack_pool: dict[str, dict[str, Any]] = field(default_factory=build_initial_rack_pool)
    work_positions: dict[str, str | None] = field(default_factory=lambda: {"SINGLE_LAYER_A": None})
    recent_operations: list[dict[str, Any]] = field(default_factory=list)

    def reset(self) -> None:
        self.rack_pool = build_initial_rack_pool()
        self.work_positions = {"SINGLE_LAYER_A": None}
        self.recent_operations = []

    async def reset_locked(self) -> None:
        async with self.lock:
            self.reset()

    async def snapshot(self) -> dict[str, Any]:
        async with self.lock:
            return self.snapshot_unlocked()

    def snapshot_unlocked(self) -> dict[str, Any]:
        return {
            "racks": {rack_id: dict(rack) for rack_id, rack in self.rack_pool.items()},
            "work_positions": dict(self.work_positions),
            "recent_operations": [dict(operation) for operation in self.recent_operations],
        }

    async def get_rack(self, rack_id: str) -> dict[str, Any] | None:
        async with self.lock:
            rack = self.rack_pool.get(rack_id)
            if rack is None:
                return None
            return dict(rack)

    async def list_racks(self, rack_type: str | None = None) -> list[dict[str, Any]]:
        async with self.lock:
            racks = [dict(rack) for rack in self.rack_pool.values()]
        if rack_type:
            return [rack for rack in racks if rack["rack_type"] == rack_type]
        return racks

    async def set_rack_status(
        self,
        rack_id: str,
        *,
        status: str,
        current_location: str | None = None,
    ) -> dict[str, Any] | None:
        async with self.lock:
            rack = self.rack_pool.get(rack_id)
            if rack is None:
                return None
            rack["status"] = status
            if current_location is not None:
                rack["current_location"] = current_location
            return dict(rack)

    async def apply_operation(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self.lock:
            callback_payload: dict[str, Any] | None = None
            for task in _rack_operation_tasks(payload):
                task_type = str(task.get("task_type") or "")
                task_payload = _rack_operation_task_payload(
                    payload, task, derive_dispatch_key=_has_rack_operation_tasks(payload)
                )
                if task_type == "MOVE_OUT_ACTIVE_RACK":
                    failure_payload = self._move_out_active_rack(task_payload)
                    if failure_payload is not None:
                        return failure_payload
                    continue
                if task_type == "ALLOCATE_AND_MOVE_RACK":
                    callback_payload = self._allocate_rack_for_payload_unlocked(task_payload)
                    if callback_payload["callback_type"] == "WMS_RACK_EXCHANGE_FAILED":
                        return callback_payload
                    continue
                if task_type == "MOVE_RACK":
                    self._move_rack(task_payload)
                    callback_payload = _rack_operation_callback_payload(task_payload)

            if callback_payload is not None:
                return callback_payload
            return _rack_operation_callback_payload(payload)

    async def allocate_rack_for_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self.lock:
            return self._allocate_rack_for_payload_unlocked(payload)

    def _allocate_rack_for_payload_unlocked(self, payload: dict[str, Any]) -> dict[str, Any]:
        target_position_code = str(payload.get("target_position_code") or "SINGLE_LAYER_A")
        active_rack_code = self.work_positions.get(target_position_code)
        if active_rack_code:
            return build_rack_operation_failure_payload(
                payload,
                reason_code="TARGET_POSITION_OCCUPIED",
                reason_message=f"目标工位 {target_position_code} 已有活动货架 {active_rack_code}",
            )

        required_bin_type = _rack_operation_supplied_bin_type(payload)
        requested_rack_code = str(payload.get("rack_code") or "").strip()
        if requested_rack_code:
            requested_layout = _known_rack_layout(requested_rack_code)
            if requested_layout is None or requested_layout["bin_type"] != required_bin_type:
                return build_rack_operation_failure_payload(
                    payload,
                    reason_code="RACK_LAYOUT_MISMATCH",
                    reason_message=f"指定货架 {requested_rack_code} 不匹配 {required_bin_type}",
                )

        rack_code = self._next_available_rack_code(required_bin_type, preferred_rack_code=requested_rack_code or None)
        if rack_code is None:
            return build_rack_operation_failure_payload(
                payload,
                reason_code="NO_AVAILABLE_RACK",
                reason_message=f"没有可用的 {required_bin_type} 货架",
            )

        allocated_payload = dict(payload)
        allocated_payload["rack_code"] = rack_code
        allocated_payload["callback_type"] = "WMS_RACK_ARRIVED"
        callback_payload = _rack_operation_callback_payload(allocated_payload)

        operation_key = str(callback_payload.get("operation_key") or "")
        rack = self.rack_pool[rack_code]
        rack["status"] = "ACTIVE"
        rack["active_position_code"] = target_position_code
        rack["allocated_operation_key"] = operation_key
        self.work_positions[target_position_code] = rack_code
        self._record_operation(
            {
                "operation_key": operation_key,
                "dispatch_key": callback_payload["dispatch_key"],
                "task_type": callback_payload.get("task_type"),
                "rack_code": rack_code,
                "target_position_code": target_position_code,
            }
        )

        return callback_payload

    def _move_out_active_rack(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        source_position_code = str(
            payload.get("source_position_code") or payload.get("target_position_code") or "SINGLE_LAYER_A"
        )
        rack_code = str(payload.get("rack_code") or self.work_positions.get(source_position_code) or "")
        if not rack_code or rack_code not in self.rack_pool:
            return build_rack_operation_failure_payload(
                payload,
                reason_code="ACTIVE_RACK_NOT_FOUND",
                reason_message=f"工位 {source_position_code} 没有可移出的活动货架 {rack_code or ''}".strip(),
            )
        active_rack_code = self.work_positions.get(source_position_code)
        if active_rack_code != rack_code:
            active_rack_message = active_rack_code or "无"
            return build_rack_operation_failure_payload(
                payload,
                reason_code="MOVE_OUT_RACK_MISMATCH",
                reason_message=f"工位 {source_position_code} 当前活动货架为 {active_rack_message}，不是 {rack_code}",
            )
        rack = self.rack_pool[rack_code]
        rack["status"] = "MOVED_OUT"
        rack["active_position_code"] = None
        rack["allocated_operation_key"] = None
        if self.work_positions.get(source_position_code) == rack_code:
            self.work_positions[source_position_code] = None
        self._record_operation(
            {
                "operation_key": payload.get("operation_key"),
                "dispatch_key": payload.get("dispatch_key"),
                "task_type": "MOVE_OUT_ACTIVE_RACK",
                "rack_code": rack_code,
                "source_position_code": source_position_code,
            }
        )
        return None

    def _move_rack(self, payload: dict[str, Any]) -> None:
        rack_code = str(payload.get("rack_code") or "")
        if rack_code not in self.rack_pool:
            return
        rack = self.rack_pool[rack_code]
        target_position_code = payload.get("target_position_code")
        if isinstance(target_position_code, str) and target_position_code:
            rack["current_location"] = target_position_code
            rack["status"] = "MOVED_OUT"
            rack["active_position_code"] = None
            for position_code, active_rack_code in self.work_positions.items():
                if active_rack_code == rack_code:
                    self.work_positions[position_code] = None
            self._record_operation(
                {
                    "operation_key": payload.get("operation_key"),
                    "dispatch_key": payload.get("dispatch_key"),
                    "task_type": "MOVE_RACK",
                    "rack_code": rack_code,
                    "target_position_code": target_position_code,
                }
            )

    def _record_operation(self, operation: dict[str, Any]) -> None:
        self.recent_operations.append(operation)
        if len(self.recent_operations) > RECENT_OPERATION_LIMIT:
            del self.recent_operations[: len(self.recent_operations) - RECENT_OPERATION_LIMIT]

    def _next_available_rack_code(self, required_bin_type: str, preferred_rack_code: str | None = None) -> str | None:
        if preferred_rack_code is not None:
            preferred_rack = self.rack_pool.get(preferred_rack_code)
            preferred_layout = _known_rack_layout(preferred_rack_code)
            if (
                preferred_rack is not None
                and preferred_rack["status"] == "AVAILABLE"
                and preferred_layout is not None
                and preferred_layout["bin_type"] == required_bin_type
            ):
                return preferred_rack_code
            return None
        for rack_code, rack in self.rack_pool.items():
            if rack["status"] != "AVAILABLE":
                continue
            layout = _known_rack_layout(rack_code)
            if layout is not None and layout["bin_type"] == required_bin_type:
                return rack_code
        return None


mock_wms_state = MockWmsState()


def reset_mock_wms_state() -> None:
    mock_wms_state.reset()
    fault_injection_state["next_status"] = 200
    fault_injection_state["next_delay"] = 0.0


def _mock_wms_debug_snapshot() -> dict[str, Any]:
    return mock_wms_state.snapshot_unlocked()


def _rack_operation_tasks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_tasks = payload.get("rack_tasks") or payload.get("task_specs")
    if isinstance(raw_tasks, list):
        tasks = [dict(task) for task in raw_tasks if isinstance(task, dict)]
        return sorted(tasks, key=lambda task: int(task.get("sequence_no") or 0))
    return [
        {
            "sequence_no": payload.get("sequence_no"),
            "task_type": payload.get("task_type"),
            "rack_code": payload.get("rack_code"),
            "rack_kind": payload.get("rack_kind"),
            "target_position_code": payload.get("target_position_code"),
            "source_position_code": payload.get("source_position_code"),
        }
    ]


def _has_rack_operation_tasks(payload: dict[str, Any]) -> bool:
    return isinstance(payload.get("rack_tasks") or payload.get("task_specs"), list)


def _rack_operation_task_payload(
    payload: dict[str, Any],
    task: dict[str, Any],
    *,
    derive_dispatch_key: bool,
) -> dict[str, Any]:
    task_payload = {**payload, **task}
    task_type = str(task_payload.get("task_type") or "")
    sequence_no = task_payload.get("sequence_no")
    operation_key = str(task_payload.get("operation_key") or "")
    if derive_dispatch_key and operation_key and sequence_no is not None and task_type:
        task_dispatch_key = f"rack-operation:{operation_key}:{sequence_no}:{task_type}"
        task_payload["dispatch_key"] = task.get("dispatch_key") or task_dispatch_key
        task_payload["request_id"] = task.get("request_id") or task_payload["dispatch_key"]
    return task_payload


# ============================================
# FastAPI 应用
# ============================================

app = FastAPI(title="WMS Mock Server", description="模拟 WMS 主数据查询及库存操作接口", version="1.0.0")


@app.middleware("http")
async def fault_injection_middleware(request: Request, call_next):
    if request.url.path.startswith("/debug"):
        return await call_next(request)

    if fault_injection_state["next_delay"] > 0:
        await asyncio.sleep(fault_injection_state["next_delay"])
        fault_injection_state["next_delay"] = 0.0

    if fault_injection_state["next_status"] != 200:
        status = fault_injection_state["next_status"]
        fault_injection_state["next_status"] = 200
        return Response(
            content='{"code": ' + str(status) + ', "message": "Fault Injected"}',
            status_code=status,
            media_type="application/json",
        )

    return await call_next(request)


# --- Debug/Mock 接口 ---


class SimulateFailureRequest(BaseModel):
    status: int = 500
    delay: float = 0.0


class RackStatusRequest(BaseModel):
    status: str
    current_location: str | None = None


@app.post("/debug/simulate-failure", summary="注入 HTTP 故障")
async def simulate_failure(request: SimulateFailureRequest):
    """
    配置下一次外部调用（非 /debug）将返回指定的 HTTP 状态码并等待 delay 秒。
    这适用于触发 WES 端的 Circuit Breaker 和 WmsUnavailableError 测试。
    """
    fault_injection_state["next_status"] = request.status
    fault_injection_state["next_delay"] = request.delay
    return {"message": f"Next request will return {request.status} after {request.delay}s"}


@app.post("/debug/reset", summary="恢复 WMS Mock 初始状态")
async def debug_reset():
    await mock_wms_state.reset_locked()
    fault_injection_state["next_status"] = 200
    fault_injection_state["next_delay"] = 0.0
    return {"code": 200, "data": {"reset": True}}


@app.get("/debug/racks", summary="查看 WMS Mock 货架状态")
async def debug_racks():
    return {"code": 200, "data": await mock_wms_state.snapshot()}


@app.post("/debug/racks/{rack_id}/status", summary="设置 WMS Mock 货架状态")
async def debug_set_rack_status(rack_id: str, request: RackStatusRequest):
    rack = await mock_wms_state.set_rack_status(
        rack_id,
        status=request.status,
        current_location=request.current_location,
    )
    if rack is None:
        return Response(
            status_code=404,
            content='{"code": 404, "message": "Rack Not Found"}',
            media_type="application/json",
        )
    return {"code": 200, "data": rack}


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)


async def _post_callback(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
        return {
            "delivered": 200 <= response.status_code < 300,
            "status_code": response.status_code,
            "response_text": response.text,
        }
    except Exception as exc:
        logger.error("WMS Mock 回调 WES 失败: %s", exc)
        return {"delivered": False, "error": str(exc)}


def _rack_operation_callback_payload(payload: dict[str, Any]) -> dict[str, Any]:
    dispatch_key = str(payload.get("dispatch_key") or payload.get("request_id") or "")
    operation_key = str(payload.get("operation_key") or "")
    rack_kind = payload.get("rack_kind") or "SINGLE_LAYER"
    target_position_code = payload.get("target_position_code") or "SINGLE_LAYER_A"
    callback_type = str(payload.get("callback_type") or "WMS_RACK_ARRIVED")
    timestamp = _now_ms()
    source_key = dispatch_key or operation_key or repr(sorted(payload.items()))
    source_event_hash = hashlib.sha256(source_key.encode()).hexdigest()[:16]
    required_bin_type = _rack_operation_supplied_bin_type(payload)
    rack_code = _rack_operation_rack_code(payload, required_bin_type)
    rack_physical_payload = build_active_bin_rack_payload(rack_code=rack_code, rack_kind=rack_kind)
    callback_payload = {
        **payload,
        "callback_type": callback_type,
        "dispatch_key": dispatch_key,
        "request_id": str(payload.get("request_id") or dispatch_key),
        "operation_key": operation_key,
        "source_system": "WMS",
        "source_event_id": payload.get("source_event_id") or f"wms-mock:rack-operation:{source_event_hash}",
        "source_version": "mock-wms.v1",
        "occurred_at": timestamp,
        "timestamp": timestamp,
        "signature": "mock-signature",
        "rack_code": rack_code,
        "rack_kind": rack_kind,
        "position_code": target_position_code,
        "target_position_code": target_position_code,
        **rack_physical_payload,
    }
    if callback_type in RACK_STATUS_CALLBACK_TYPES and not any(
        str(callback_payload.get(field) or "").strip() for field in RACK_STATUS_FIELDS
    ):
        callback_payload["status"] = "SUCCESS"
        callback_payload["task_status"] = "SUCCESS"
        callback_payload["result"] = "SUCCESS"
    return callback_payload


def build_rack_operation_failure_payload(
    payload: dict[str, Any],
    *,
    reason_code: str,
    reason_message: str,
) -> dict[str, Any]:
    dispatch_key = str(payload.get("dispatch_key") or payload.get("request_id") or "")
    operation_key = str(payload.get("operation_key") or "")
    timestamp = _now_ms()
    source_key = dispatch_key or operation_key or repr(sorted(payload.items()))
    source_event_hash = hashlib.sha256(source_key.encode()).hexdigest()[:16]
    target_position_code = payload.get("target_position_code") or "SINGLE_LAYER_A"
    return {
        **payload,
        "callback_type": "WMS_RACK_EXCHANGE_FAILED",
        "dispatch_key": dispatch_key,
        "request_id": str(payload.get("request_id") or dispatch_key),
        "operation_key": operation_key,
        "source_system": "WMS",
        "source_event_id": payload.get("source_event_id") or f"wms-mock:rack-operation:{source_event_hash}",
        "source_version": "mock-wms.v1",
        "occurred_at": timestamp,
        "timestamp": timestamp,
        "signature": "mock-signature",
        "position_code": target_position_code,
        "target_position_code": target_position_code,
        "status": "FAILED",
        "task_status": "FAILED",
        "result": "FAILED",
        "reason_code": reason_code,
        "reason_message": reason_message,
        "error_code": reason_code,
        "error_message": reason_message,
    }


def build_active_bin_rack_payload(rack_code: str, rack_kind: str = "SINGLE_LAYER") -> dict[str, Any]:
    layout = _known_rack_layout(rack_code)
    if layout is None:
        raise ValueError(f"未知 WMS mock rack: {rack_code}")
    bin_type = str(layout["bin_type"])
    bin_prefix = str(layout["bin_prefix"])
    cell_indexes = tuple(layout["cell_indexes"])
    bin_mounts = [
        {"rack_code": rack_code, "rack_slot_code": slot_code, "bin_code": f"{bin_prefix}-{index:03d}"}
        for index, slot_code in enumerate(RACK_SLOT_CODES, start=1)
    ]
    cells = [
        {
            "rack_slot_code": mount["rack_slot_code"],
            "rack_slot_location_code": f"{rack_code}-1{mount['rack_slot_code']}-0",
            "bin_code": mount["bin_code"],
            "bin_id": mount["bin_code"],
            "bin_type": bin_type,
            "bin_orientation_code": f"{mount['bin_code']}-A",
            "bin_cell_index": cell_index,
            "bin_cell_location": f"{mount['bin_code']}-{cell_index}",
            "capacity_depth_mm": _rack_operation_cell_capacity_depth(bin_type, cell_index),
            "used_depth_mm": 0.0,
            "status": "EMPTY",
        }
        for mount in bin_mounts
        for cell_index in cell_indexes
    ]
    return {
        "active_bin_rack": {
            "rack_id": rack_code,
            "rack_code": rack_code,
            "rack_kind": rack_kind,
            "rack_type": rack_kind,
            "cells": cells,
        },
        "bin_mounts": bin_mounts,
    }


def _rack_operation_supplied_bin_type(payload: dict[str, Any]) -> str:
    material = payload.get("material")
    if isinstance(material, dict) and _is_large_reel_material(material):
        return "3格箱"
    return "6格箱"


def _rack_operation_default_rack_code(bin_type: str) -> str:
    if bin_type == "3格箱":
        return "RACK-3CELL-001"
    return "RACK-001"


def _rack_operation_rack_code(payload: dict[str, Any], required_bin_type: str) -> str:
    requested_rack_code = str(payload.get("rack_code") or "").strip()
    if requested_rack_code:
        layout = _known_rack_layout(requested_rack_code)
        has_material_constraints = isinstance(payload.get("material"), dict)
        if layout is not None and (layout["bin_type"] == required_bin_type or not has_material_constraints):
            return requested_rack_code
    return _rack_operation_default_rack_code(required_bin_type)


def _is_large_reel_material(material: dict[str, Any]) -> bool:
    if _has_large_reel_size(material):
        return True

    material_id = (
        material.get("material_id")
        or material.get("material_code")
        or material.get("sku")
        or material.get("HHPN")
        or material.get("hhpn")
    )
    if material_id is not None:
        catalog_material = MOCK_MATERIALS.get(str(material_id))
        if isinstance(catalog_material, dict) and _has_large_reel_size(catalog_material):
            return True

    return False


def _has_large_reel_size(material: dict[str, Any]) -> bool:
    value = material.get("reel_diameter") or material.get("standard_reel_diameter") or material.get("diameter")
    if value is None:
        value = material.get("reel_size") or material.get("standard_dims")
    if value is None:
        return False

    text = str(value).strip().lower()
    inch_match = re.search(r"(?<!\d)(13|15)(?:\.0+)?\s*(?:in|inch|英寸)?(?!\d)", text)
    if inch_match is not None:
        return True
    try:
        diameter = float(text)
    except ValueError:
        return False
    if 10 <= diameter <= 20:
        return True
    return diameter > 220


def _rack_operation_cell_capacity_depth(bin_type: str, cell_index: str) -> float:
    if bin_type == "3格箱" and cell_index == THREE_CELL_LARGE_BIN_CELL_INDEX:
        return 80.0
    return 20.0


async def _report_rack_operation_callback(callback_payload: dict[str, Any]) -> None:
    delivery = await _post_callback(WES_EXTERNAL_CALLBACK_URL, callback_payload)
    logger.info("WMS Mock rack operation callback delivery: %s", delivery)


# --- 主数据查询接口 (WMS Master Data) ---


@app.get("/api/wms/materials/{material_id}")
async def get_material(material_id: str):
    if material_id in MOCK_MATERIALS:
        return {"code": 200, "data": MOCK_MATERIALS[material_id]}
    return Response(
        status_code=404, content='{"code": 404, "message": "Material Not Found"}', media_type="application/json"
    )


@app.get("/api/wms/materials")
async def get_materials_batch(ids: str):
    id_list = ids.split(",")
    data = [MOCK_MATERIALS[m_id] for m_id in id_list if m_id in MOCK_MATERIALS]
    return {"code": 200, "data": data}


@app.get("/api/wms/zones")
async def get_zones():
    return {"code": 200, "data": MOCK_ZONES}


@app.get("/api/wms/locations")
async def get_locations(zone: str):
    locs = [location for location in MOCK_LOCATIONS if location["zone_code"] == zone]
    return {"code": 200, "data": locs}


@app.get("/api/wms/racks/{rack_id}")
async def get_rack(rack_id: str):
    rack = await mock_wms_state.get_rack(rack_id)
    if rack is not None:
        return {"code": 200, "data": rack}
    return Response(
        status_code=404, content='{"code": 404, "message": "Rack Not Found"}', media_type="application/json"
    )


@app.get("/api/wms/racks")
async def get_racks(type: str | None = None):
    return {"code": 200, "data": await mock_wms_state.list_racks(type)}


@app.post("/api/wms/rack-operation", summary="接收货架操作请求并回调 WES")
async def rack_operation(payload: dict[str, Any], background_tasks: BackgroundTasks):
    callback_payload = await mock_wms_state.apply_operation(payload)
    background_tasks.add_task(_report_rack_operation_callback, callback_payload)
    return {
        "code": 200,
        "data": {
            "accepted": True,
            "dispatch_key": payload.get("dispatch_key"),
            "operation_key": payload.get("operation_key"),
        },
    }


@app.get("/api/wms/grn/{grn_id}")
async def get_grn(grn_id: str):
    if grn_id in MOCK_GRNS:
        return {"code": 200, "data": MOCK_GRNS[grn_id]}
    return Response(status_code=404, content='{"code": 404, "message": "GRN Not Found"}', media_type="application/json")


# --- 交易接口 (Inventory / Reservations) ---


def _inventory_items(*, sku: str, lot_no: str | None = None) -> list[dict[str, Any]]:
    if not sku:
        return []
    if lot_no:
        item = MOCK_INVENTORY.get((sku, lot_no))
        return [dict(item)] if item is not None else []
    return [dict(item) for (item_sku, _), item in MOCK_INVENTORY.items() if item_sku == sku]


@app.post("/api/wms/inventory/query", summary="查询库存 (POST)")
async def query_inventory_post(payload: dict[str, Any]):
    # Note: 此为兼容当前代码 `wms_integration` 中的 post_json 方法而存在。
    material_id = str(payload.get("sku") or "")
    lot_no = payload.get("lot_no")
    return {
        "code": 200,
        "data": {"items": _inventory_items(sku=material_id, lot_no=lot_no if isinstance(lot_no, str) else None)},
    }


@app.get("/api/wms/inventory/query", summary="查询库存 (GET)")
async def query_inventory_get(material_id: str | None = None, sku: str | None = None, lot_no: str | None = None):
    # 此为符合文档白皮书的标准 GET 路由
    target_sku = sku or material_id or ""
    return {
        "code": 200,
        "data": {"items": _inventory_items(sku=target_sku, lot_no=lot_no)},
    }


@app.post("/api/wms/inventory/reserve")
async def reserve_inventory(payload: dict[str, Any]):
    return {
        "code": 200,
        "data": {
            "request_id": payload.get("request_id", ""),
            "reservation_key": payload.get("reservation_key", "RES-001"),
            "accepted": True,
        },
    }


@app.post("/api/wms/inventory/reservations/release")
async def release_reservation(payload: dict[str, Any]):
    return {
        "code": 200,
        "data": {
            "request_id": payload.get("request_id", ""),
            "reservation_key": payload.get("reservation_key", ""),
            "released": True,
        },
    }


@app.delete("/api/wms/inventory/reserve/{reservation_key}")
async def delete_reservation(reservation_key: str):
    return {
        "code": 200,
        "data": {
            "reservation_key": reservation_key,
            "released": True,
        },
    }


@app.post("/api/wms/inbound/confirm")
async def confirm_inbound(payload: dict[str, Any]):
    return {
        "code": 200,
        "data": {
            "request_id": payload.get("request_id", ""),
            "inbound_key": payload.get("inbound_key", ""),
            "confirmed": True,
        },
    }


@app.post("/api/wms/outbound/confirm")
async def confirm_outbound(payload: dict[str, Any]):
    return {
        "code": 200,
        "data": {
            "request_id": payload.get("request_id", ""),
            "outbound_key": payload.get("outbound_key", ""),
            "confirmed": True,
        },
    }


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
        self.config = Config(app=app, host=host, port=port, log_level="info")

    async def start(self) -> None:
        logger.info(f"WMS Mock 服务启动: http://{self.host}:{self.port}")
        logger.info("  - 模拟物料数: %d", len(MOCK_MATERIALS))
        logger.info("  - 模拟区域数: %d", len(MOCK_ZONES))
        logger.info("  - 模拟货架数: %d", len(mock_wms_state.rack_pool))
        server = Server(self.config)
        await server.serve()

    def run(self):
        asyncio.run(self.start())


if __name__ == "__main__":
    server = WmsMockServer()
    server.run()
