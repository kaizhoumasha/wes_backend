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
import hmac
import importlib.util
import json
import logging
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Optional
from uuid import uuid4

import httpx
from fastapi import BackgroundTasks, FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import ClientDisconnect
from uvicorn import Config, Server

from src.app.wms_integration.operation_contract import WmsCompletionMode
from src.app.wms_integration.operation_registry import WMS_OPERATION_BY_IDENTITY, WMS_OPERATIONS

# 添加项目根目录到 sys.path
project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# 北向 contract 核心必须同时支持 pytest package import 与 Docker 的脚本入口。
from tests.mock.wms_northbound_contract import (
    ACTIVE_MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE,
    NorthboundAuthError,
    NorthboundHmacReplayGuard,
    NorthboundOperationStore,
    NorthboundPayloadValidationError,
    build_typed_ack,
    build_typed_result,
    canonical_payload_bytes,
    content_sha256,
    validate_typed_request,
    verify_status_hmac,
    verify_submit_hmac,
)

_ASYNC_SUBMIT_DEADLINES = frozenset(
    operation.budget.deadline_seconds
    for operation in WMS_OPERATION_BY_IDENTITY.values()
    if operation.completion_mode is WmsCompletionMode.ASYNC_TASK
)
if len(_ASYNC_SUBMIT_DEADLINES) != 1:
    raise RuntimeError("frozen ASYNC_TASK operations must share one submit deadline")
_DEFAULT_ASYNC_SUBMIT_DEADLINE = str(next(iter(_ASYNC_SUBMIT_DEADLINES)))


def _load_sandbox_catalog() -> Any:
    """按文件加载共享 catalog，避免导入完整后端运行时包。"""

    catalog_path = project_root / "src" / "app" / "runtime" / "orchestration" / "sandbox_catalog_bridge.py"
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

CALLBACK_API_APP_ID = os.getenv("API_APP_ID", "")
CALLBACK_API_APP_SECRET = os.getenv("API_APP_SECRET", "")


def _positive_float_env(name: str) -> float | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        value = float(raw)
    except ValueError:
        logger.warning("忽略无效的 %s=%r，使用 Mock WMS 默认格位容量", name, raw)
        return None
    if not math.isfinite(value):
        logger.warning("忽略非有限数 %s=%r，使用 Mock WMS 默认格位容量", name, raw)
        return None
    if value <= 0:
        logger.warning("忽略非正数 %s=%r，使用 Mock WMS 默认格位容量", name, raw)
        return None
    return value


MOCK_WMS_CELL_CAPACITY_DEPTH_MM = _positive_float_env("MOCK_WMS_CELL_CAPACITY_DEPTH_MM")

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
        "bin_type": "混合料箱",
        "supported_bin_types": ("6格箱", "3格箱"),
        "layout_code": "MIXED",
        "bins": (
            {
                "rack_slot_code": "A",
                "bin_code": "BIN-001",
                "bin_type": "6格箱",
                "cell_indexes": SEVEN_INCH_BIN_CELL_INDEXES,
            },
            {
                "rack_slot_code": "B",
                "bin_code": "BIN-002",
                "bin_type": "6格箱",
                "cell_indexes": SEVEN_INCH_BIN_CELL_INDEXES,
            },
            {
                "rack_slot_code": "C",
                "bin_code": "BIN-003",
                "bin_type": "3格箱",
                "cell_indexes": THREE_CELL_BIN_CELL_INDEXES,
            },
            {
                "rack_slot_code": "D",
                "bin_code": "BIN-004",
                "bin_type": "3格箱",
                "cell_indexes": THREE_CELL_BIN_CELL_INDEXES,
            },
        ),
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


def _rack_layout_supports_bin_type(layout: dict[str, Any], required_bin_type: str) -> bool:
    supported = layout.get("supported_bin_types")
    if isinstance(supported, (list, tuple, set)):
        return required_bin_type in {str(item) for item in supported}
    return str(layout.get("bin_type") or "") == required_bin_type


def _rack_layout_supports_auto_allocation(layout: dict[str, Any], required_bin_type: str) -> bool:
    if required_bin_type == "3格箱":
        return str(layout.get("bin_type") or "") == required_bin_type
    return _rack_layout_supports_bin_type(layout, required_bin_type)


def _rack_layout_bins(rack_code: str, layout: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    bins = layout.get("bins")
    if isinstance(bins, (list, tuple)):
        return tuple(dict(bin_spec) for bin_spec in bins if isinstance(bin_spec, dict))

    bin_type = str(layout["bin_type"])
    bin_prefix = str(layout["bin_prefix"])
    cell_indexes = tuple(layout["cell_indexes"])
    return tuple(
        {
            "rack_slot_code": slot_code,
            "bin_code": f"{bin_prefix}-{index:03d}",
            "bin_type": bin_type,
            "cell_indexes": cell_indexes,
        }
        for index, slot_code in enumerate(RACK_SLOT_CODES, start=1)
    )


def _build_rack_state(rack_id: str) -> dict[str, Any]:
    layout = _known_rack_layout(rack_id)
    if layout is None:
        raise ValueError(f"未知 WMS mock rack: {rack_id}")
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
            if requested_layout is None or not _rack_layout_supports_bin_type(requested_layout, required_bin_type):
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
                and _rack_layout_supports_bin_type(preferred_layout, required_bin_type)
            ):
                return preferred_rack_code
            return None
        for rack_code, rack in self.rack_pool.items():
            if rack["status"] != "AVAILABLE":
                continue
            layout = _known_rack_layout(rack_code)
            if layout is not None and _rack_layout_supports_auto_allocation(layout, required_bin_type):
                return rack_code
        return None


mock_wms_state = MockWmsState()

# 北向 contract 记录独立于遗留货架状态，但必须随同 Mock reset 清理。
northbound_clock_state: dict[str, datetime | None] = {"now": None}


@dataclass(frozen=True, slots=True)
class _NorthboundFault:
    status: int
    target_path: str
    method: str
    operation_identity: str | None
    retry_after: int | None
    delay: float
    after_response: bool
    not_found: bool
    max_response_bytes: int | None
    response_body_bytes: int | None


northbound_fault_lock = RLock()
northbound_fault_state: dict[str, _NorthboundFault | None] = {"next": None}
northbound_callback_hint_evidence: dict[tuple[str, str], dict[str, str]] = {}


def _northbound_now() -> datetime:
    return northbound_clock_state["now"] or datetime.now(UTC)


def _positive_finite_env_float(name: str, default: str) -> float:
    value = float(os.getenv(name, default))
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return value


northbound_operation_store = NorthboundOperationStore(
    clock=_northbound_now,
    retention_seconds=int(os.getenv("WMS_EFFECT_IDEMPOTENCY_RETENTION_SECONDS", "9")),
    visibility_sla_seconds=_positive_finite_env_float("WMS_EFFECT_STATUS_VISIBILITY_SLA_SECONDS", "2"),
)
northbound_hmac_replay_guard = NorthboundHmacReplayGuard()


def reset_mock_wms_state() -> None:
    mock_wms_state.reset()
    northbound_operation_store.reset()
    northbound_hmac_replay_guard.reset()
    northbound_clock_state["now"] = None
    with northbound_fault_lock:
        northbound_fault_state["next"] = None
    northbound_callback_hint_evidence.clear()
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


def _northbound_fault_matches(fault: _NorthboundFault, request: Request) -> bool:
    if request.method != fault.method or request.url.path != fault.target_path:
        return False
    if fault.operation_identity is None:
        return True
    actual_identity = request.headers.get("X-WES-Operation-Identity") or request.query_params.get("operation_identity")
    return actual_identity == fault.operation_identity


def _northbound_not_found_payload() -> dict[str, Any]:
    return {
        "state": "NOT_FOUND",
        "provider_reference": None,
        "reason_code": None,
        "updated_at": None,
        "source_version": None,
        "result_payload": None,
    }


def _northbound_fault_response(fault: _NorthboundFault) -> Response:
    if 500 <= fault.status <= 599:
        code = "TEMPORARILY_UNAVAILABLE"
    elif fault.status == 429:
        code = "RATE_LIMITED"
    else:
        code = "NORTHBOUND_FAULT"
    prefix = json.dumps({"code": code}, separators=(",", ":")).encode()
    headers = {"Retry-After": str(fault.retry_after)} if fault.status == 429 and fault.retry_after is not None else None
    if fault.response_body_bytes is None:
        content = prefix if fault.max_response_bytes is None else prefix[: fault.max_response_bytes]
        return Response(content=content, status_code=fault.status, media_type="application/json", headers=headers)

    async def stream_body():
        remaining_budget = fault.max_response_bytes
        prefix_chunk = prefix if remaining_budget is None else prefix[:remaining_budget]
        if prefix_chunk:
            yield prefix_chunk
        if remaining_budget is not None:
            remaining_budget = max(remaining_budget - len(prefix_chunk), 0)
        remaining_filler = fault.response_body_bytes
        if remaining_budget is not None:
            remaining_filler = min(remaining_filler, remaining_budget)
        while remaining_filler > 0:
            chunk_size = min(remaining_filler, 1024)
            yield b"x" * chunk_size
            remaining_filler -= chunk_size

    return StreamingResponse(stream_body(), status_code=fault.status, media_type="application/json", headers=headers)


@app.middleware("http")
async def fault_injection_middleware(request: Request, call_next):
    if request.url.path.startswith("/debug"):
        return await call_next(request)

    # 一次性 fault 必须在首个 await 前由同步锁原子认领，避免并发请求重复消费。
    claimed_fault: _NorthboundFault | None = None
    with northbound_fault_lock:
        configured_fault = northbound_fault_state["next"]
        if configured_fault is not None and _northbound_fault_matches(configured_fault, request):
            claimed_fault = configured_fault
            northbound_fault_state["next"] = None

    if claimed_fault is not None and claimed_fault.after_response:
        response = await call_next(request)
        if claimed_fault.delay > 0:
            await asyncio.sleep(claimed_fault.delay)
        return response

    if claimed_fault is not None:
        if claimed_fault.delay > 0:
            await asyncio.sleep(claimed_fault.delay)
        if claimed_fault.not_found:
            return JSONResponse(status_code=200, content=_northbound_not_found_payload())
        if claimed_fault.status != 200 or claimed_fault.response_body_bytes is not None:
            return _northbound_fault_response(claimed_fault)

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


class NorthboundFaultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: int = 500
    target_path: str
    method: str
    operation_identity: str | None = None
    retry_after: int | None = None
    delay: float = Field(default=0.0, ge=0)
    after_response: bool = False
    not_found: bool = False
    max_response_bytes: int | None = Field(default=None, ge=1, le=16 * 1024 * 1024)
    response_body_bytes: int | None = Field(default=None, ge=0, le=16 * 1024 * 1024)


class NorthboundRejectRequest(BaseModel):
    operation_identity: str
    idempotency_key: str
    reason_code: str


class NorthboundClockRequest(BaseModel):
    now: str | None = None


class NorthboundVisibilityRequest(BaseModel):
    operation_identity: str
    idempotency_key: str
    delay_seconds: float = Field(ge=0, allow_inf_nan=False)


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
    northbound_operation_store.reset()
    northbound_hmac_replay_guard.reset()
    northbound_clock_state["now"] = None
    with northbound_fault_lock:
        northbound_fault_state["next"] = None
    northbound_callback_hint_evidence.clear()
    fault_injection_state["next_status"] = 200
    fault_injection_state["next_delay"] = 0.0
    return {"code": 200, "data": {"reset": True}}


@app.post("/debug/northbound/faults", summary="注入北向 HTTP 故障")
async def debug_set_northbound_fault(request: NorthboundFaultRequest):
    """仅供 Mock 验收模拟限流、服务端错误、慢响应与响应体边界。"""

    method = request.method.strip().upper()
    target_path = request.target_path.strip()
    if method not in {"GET", "POST"} or not target_path.startswith(
        ("/northbound/", "/api/wms/inventory/confirm-inbound", "/api/wms/fulfillment/")
    ):
        return JSONResponse(status_code=422, content={"code": "INVALID_NORTHBOUND_FAULT_SCOPE"})
    configured = _NorthboundFault(
        status=request.status,
        target_path=target_path,
        method=method,
        operation_identity=request.operation_identity,
        retry_after=request.retry_after,
        delay=request.delay,
        after_response=request.after_response,
        not_found=request.not_found,
        max_response_bytes=request.max_response_bytes,
        response_body_bytes=request.response_body_bytes,
    )
    with northbound_fault_lock:
        northbound_fault_state["next"] = configured
    return {
        "code": 200,
        "data": {
            "status": configured.status,
            "target_path": configured.target_path,
            "method": configured.method,
            "operation_identity": configured.operation_identity,
            "retry_after": configured.retry_after,
            "delay": configured.delay,
            "after_response": configured.after_response,
            "not_found": configured.not_found,
            "max_response_bytes": configured.max_response_bytes,
            "response_body_bytes": configured.response_body_bytes,
        },
    }


@app.post("/debug/northbound/reject", summary="将北向请求置为业务拒绝")
async def debug_reject_northbound_operation(request: NorthboundRejectRequest):
    snapshot = northbound_operation_store.reject(
        request.operation_identity,
        request.idempotency_key,
        reason_code=request.reason_code,
    )
    return snapshot.as_dict()


@app.post("/debug/northbound/clock", summary="设置北向 Mock 时钟")
async def debug_set_northbound_clock(request: NorthboundClockRequest):
    if request.now is None:
        northbound_clock_state["now"] = None
    else:
        parsed = datetime.fromisoformat(request.now)
        if parsed.tzinfo is None:
            return JSONResponse(status_code=422, content={"code": "CLOCK_MUST_BE_TIMEZONE_AWARE"})
        northbound_clock_state["now"] = parsed.astimezone(UTC)
    return {
        "code": 200,
        "data": {"now": northbound_clock_state["now"].isoformat() if northbound_clock_state["now"] else None},
    }


@app.post("/debug/northbound/visibility", summary="设置北向状态暂时不可见次数")
async def debug_set_northbound_visibility(request: NorthboundVisibilityRequest):
    """仅供黑盒探针按公开时钟模拟受理后在 SLA 内暂时返回 NOT_FOUND。"""

    try:
        northbound_operation_store.configure_visibility_delay(
            request.operation_identity,
            request.idempotency_key,
            delay_seconds=request.delay_seconds,
        )
    except ValueError:
        return JSONResponse(status_code=422, content={"code": "VISIBILITY_DELAY_OUTSIDE_SLA"})
    return {
        "operation_identity": request.operation_identity,
        "idempotency_key": request.idempotency_key,
        "delay_seconds": request.delay_seconds,
    }


@app.get("/debug/northbound/effects", summary="查询北向业务效果计数")
async def debug_northbound_effect_count(operation_identity: str, idempotency_key: str):
    return {
        "operation_identity": operation_identity,
        "idempotency_key": idempotency_key,
        "effect_count": northbound_operation_store.effect_count(operation_identity, idempotency_key),
    }


@app.get("/debug/northbound/callback-hints", summary="查询北向 callback hint 脱敏投影")
async def debug_northbound_callback_hints(operation_identity: str, idempotency_key: str):
    hint = northbound_callback_hint_evidence.get((operation_identity, idempotency_key))
    return {"hints": [] if hint is None else [hint]}


async def _request_body_or_none(request: Request) -> bytes | None:
    """客户端已因 deadline 断开时安静终止，不把预期超时记录成 ASGI 异常。"""

    try:
        return await request.body()
    except ClientDisconnect:
        return None


@app.get("/northbound/contract", summary="查询北向 Mock 合同承诺")
async def northbound_contract():
    return {
        "credential_reference": ACTIVE_MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE,
        "idempotency_retention_seconds": int(os.getenv("WMS_EFFECT_IDEMPOTENCY_RETENTION_SECONDS", "9")),
        "status_visibility_sla_seconds": _positive_finite_env_float("WMS_EFFECT_STATUS_VISIBILITY_SLA_SECONDS", "2"),
        "max_response_bytes": int(os.getenv("WMS_EFFECT_STATUS_MAX_RESPONSE_BYTES", "4096")),
        "submit_deadline_seconds": _positive_finite_env_float(
            "WMS_EFFECT_SUBMIT_TIMEOUT_SECONDS",
            _DEFAULT_ASYNC_SUBMIT_DEADLINE,
        ),
        "status_deadline_seconds": _positive_finite_env_float("WMS_EFFECT_STATUS_TIMEOUT_SECONDS", "2"),
    }


@app.get("/northbound/operations/status", summary="查询北向 typed EFFECT 权威状态")
async def northbound_operation_status(request: Request, operation_identity: str, idempotency_key: str):
    raw_path = request.scope["path"]
    query_string = request.scope.get("query_string", b"")
    if query_string:
        raw_path = f"{raw_path}?{query_string.decode('ascii')}"
    body = await _request_body_or_none(request)
    if body is None:
        return Response(status_code=499)
    try:
        verify_status_hmac(request.headers, body, method=request.method, path=raw_path)
        northbound_hmac_replay_guard.consume(
            credential_reference=request.headers["X-WMS-Credential-Reference"],
            timestamp=request.headers["X-WMS-Timestamp"],
            nonce=request.headers["X-WMS-Nonce"],
        )
        snapshot = northbound_operation_store.query(operation_identity, idempotency_key)
    except NorthboundAuthError as exc:
        return JSONResponse(status_code=401, content={"code": exc.code})
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"code": str(exc)})
    return snapshot.as_dict()


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
        if not CALLBACK_API_APP_ID or not CALLBACK_API_APP_SECRET:
            raise RuntimeError("WMS Mock callback API credential is required")
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        timestamp = str(int(time.time()))
        nonce = uuid4().hex
        body_sha256 = hashlib.sha256(body).hexdigest()
        path = httpx.URL(url).path
        canonical = f"POST\n{path}\n{timestamp}\n{nonce}\n{body_sha256}\n{CALLBACK_API_APP_ID}"
        signature = hmac.new(
            CALLBACK_API_APP_SECRET.encode(),
            canonical.encode(),
            hashlib.sha256,
        ).hexdigest()
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-App-ID": CALLBACK_API_APP_ID,
                    "X-Timestamp": timestamp,
                    "X-Nonce": nonce,
                    "X-Body-SHA256": body_sha256,
                    "X-Signature": signature,
                },
            )
        return {
            "delivered": 200 <= response.status_code < 300,
            "status_code": response.status_code,
            "response_text": response.text,
        }
    except Exception as exc:
        logger.error("WMS Mock 回调 WES 失败: %s", exc)
        return {"delivered": False, "error": str(exc)}


def _typed_effect_callback_payload(
    *,
    operation_identity: str,
    idempotency_key: str,
    request_payload: dict[str, Any],
) -> dict[str, Any]:
    dispatch_key = str(request_payload.get("dispatch_key") or "")
    return {
        "callback_type": "WMS_EFFECT_STATUS_HINT",
        "source_system": "WMS",
        "source_event_id": f"wms-mock:typed-effect:{uuid4().hex}",
        "trace_id": str(request_payload.get("trace_id") or f"wms-mock:{dispatch_key}"),
        "data": {
            "operation_identity": operation_identity,
            "idempotency_key": idempotency_key,
            "dispatch_key": dispatch_key,
        },
    }


async def _submit_northbound_effect(
    *,
    request: Request,
    background_tasks: BackgroundTasks,
    operation_identity: str,
) -> Response:
    """以共享状态核心受理 typed EFFECT，同时保留各 operation 的既有响应字段。"""

    body = await _request_body_or_none(request)
    if body is None:
        return Response(status_code=499)
    try:
        verify_submit_hmac(request.headers, body, method=request.method, path=request.url.path)
        northbound_hmac_replay_guard.consume(
            credential_reference=request.headers["X-WES-Credential-Reference"],
            timestamp=request.headers["X-WES-Timestamp"],
            nonce=request.headers["X-WES-Nonce"],
        )
        submitted_identity = str(request.headers.get("X-WES-Operation-Identity") or "")
        if submitted_identity != operation_identity:
            return JSONResponse(status_code=422, content={"code": "OPERATION_IDENTITY_MISMATCH"})
        if request.headers.get("content-type", "").partition(";")[0].strip().lower() != "application/json":
            raise NorthboundPayloadValidationError("typed request content type must be application/json")
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NorthboundPayloadValidationError("typed request must contain valid JSON") from exc
        if not isinstance(payload, dict):
            raise NorthboundPayloadValidationError("typed request must be a JSON object")
        idempotency_key = str(request.headers.get("Idempotency-Key") or "")
        validated_payload = validate_typed_request(operation_identity, payload)
        submission = northbound_operation_store.submit(
            operation_identity,
            idempotency_key,
            content_sha256(canonical_payload_bytes(validated_payload)),
            validated_payload,
        )
    except NorthboundAuthError as exc:
        return JSONResponse(status_code=401, content={"code": exc.code})
    except NorthboundPayloadValidationError:
        return JSONResponse(status_code=422, content={"code": "INVALID_TYPED_REQUEST"})
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"code": str(exc)})

    if submission.error_code is not None:
        return JSONResponse(
            status_code=submission.status_code,
            content={
                "code": submission.error_code,
                "data": submission.snapshot.as_dict() if submission.snapshot is not None else None,
            },
        )

    operation = WMS_OPERATION_BY_IDENTITY[operation_identity]
    if operation.completion_mode is WmsCompletionMode.SYNC_RESULT:
        if submission.snapshot is None or submission.snapshot.result_payload is None:
            return JSONResponse(status_code=500, content={"code": "MOCK_SYNC_RESULT_MISSING"})
        return JSONResponse(
            status_code=submission.status_code,
            content=submission.snapshot.result_payload,
        )

    data = _northbound_response_data(operation_identity, idempotency_key, validated_payload)
    if submission.status_code == 202 and northbound_operation_store.register_callback_hint(
        operation_identity, idempotency_key
    ):
        # Evidence endpoint 只保留 callback hint 的关联键投影，绝不复制终态或认证字段。
        northbound_callback_hint_evidence[(operation_identity, idempotency_key)] = {
            "callback_type": "WMS_EFFECT_STATUS_HINT",
            "dispatch_key": str(validated_payload.get("dispatch_key") or ""),
            "idempotency_key": idempotency_key,
            "operation_identity": operation_identity,
        }
        background_tasks.add_task(
            _post_callback,
            WES_EXTERNAL_CALLBACK_URL,
            _typed_effect_callback_payload(
                operation_identity=operation_identity,
                idempotency_key=idempotency_key,
                request_payload=validated_payload,
            ),
        )
    return JSONResponse(status_code=submission.status_code, content={"code": submission.status_code, "data": data})


def _northbound_response_data(
    operation_identity: str,
    idempotency_key: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """从已校验 typed payload 构造 E08–E14 共用 ACK。"""

    return build_typed_ack(operation_identity, idempotency_key, payload)


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
    layout_bins = _rack_layout_bins(rack_code, layout)
    bin_mounts = [
        {"rack_code": rack_code, "rack_slot_code": bin_spec["rack_slot_code"], "bin_code": bin_spec["bin_code"]}
        for bin_spec in layout_bins
    ]
    cells = [
        {
            "rack_slot_code": bin_spec["rack_slot_code"],
            "rack_slot_location_code": f"{rack_code}-1{bin_spec['rack_slot_code']}-0",
            "bin_code": bin_spec["bin_code"],
            "bin_id": bin_spec["bin_code"],
            "bin_type": bin_type,
            "bin_orientation_code": f"{bin_spec['bin_code']}-A",
            "bin_cell_index": cell_index,
            "bin_cell_location": f"{bin_spec['bin_code']}-{cell_index}",
            "capacity_depth_mm": _rack_operation_cell_capacity_depth(bin_type, cell_index),
            "used_depth_mm": 0.0,
            "status": "EMPTY",
        }
        for bin_spec in layout_bins
        for bin_type in (str(bin_spec["bin_type"]),)
        for cell_index in tuple(bin_spec["cell_indexes"])
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
        if layout is not None and (
            _rack_layout_supports_bin_type(layout, required_bin_type) or not has_material_constraints
        ):
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
    if MOCK_WMS_CELL_CAPACITY_DEPTH_MM is not None:
        return MOCK_WMS_CELL_CAPACITY_DEPTH_MM
    if bin_type == "3格箱" and cell_index == THREE_CELL_LARGE_BIN_CELL_INDEX:
        return 80.0
    return 20.0


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


def _string_list(payload: dict[str, Any], field_name: str) -> list[str]:
    raw_value = payload.get(field_name)
    if raw_value is None:
        return []
    if isinstance(raw_value, str):
        return [raw_value]
    if not isinstance(raw_value, list):
        return []
    return [str(item) for item in raw_value if str(item)]


@app.post("/api/wms/fulfillment/change-rack-face", summary="本机 Mock: 货架换面履约")
async def change_rack_face(payload: dict[str, Any]):
    """模拟 WmsFulfillmentPort.change_rack_face, 与满箱交换完成语义解耦。"""

    return {
        "code": 200,
        "data": {
            "environment": "LOCAL_MOCK_ONLY",
            "production_write_path": False,
            "request_id": payload.get("request_id", ""),
            "parent_request_id": payload.get("parent_request_id", ""),
            "fulfillment_action": "CHANGE_RACK_FACE",
            "rack_code": str(payload.get("rack_code") or ""),
            "from_rack_side": str(payload.get("from_rack_side") or ""),
            "to_rack_side": str(payload.get("to_rack_side") or ""),
            "independent_fulfillment": True,
            "does_not_mark_full_box_exchange_completed": True,
            "completion_policy": "CALLBACK_AND_RECONCILIATION_REQUIRED",
        },
    }


@app.post("/api/wms/fulfillment/rough-sorter-inbound-preview", summary="本机 Mock: 粗分机入库预览")
async def rough_sorter_inbound_preview(payload: dict[str, Any]):
    """表达粗分机正常流合同，拆分本地物理事实与 WMS 同步状态。"""

    local_physical_completed = bool(payload.get("local_physical_completed"))
    wms_pkg_binding_result = str(payload.get("wms_pkg_binding_result") or "ACCEPTED").upper()
    wms_sync_state = "READY_TO_SYNC"
    business_completion_state = "LOCAL_PHYSICAL_COMPLETED"
    if local_physical_completed and wms_pkg_binding_result not in {"ACCEPTED", "CONFIRMED"}:
        wms_sync_state = "WMS_SYNC_PENDING"
        business_completion_state = "RECONCILING"
    return {
        "code": 200,
        "data": {
            "environment": "LOCAL_MOCK_ONLY",
            "production_write_path": False,
            "request_id": payload.get("request_id", ""),
            "object_key": payload.get("object_key", ""),
            "target_cell_code": payload.get("target_cell_code", ""),
            "ordered_steps": [
                "SCAN_AND_MEASURE",
                "WMS_GRN_BINDING_CHECK",
                "SOURCE_ARM_TO_CONVEYOR",
                "ROUGH_SORTER_TO_OUTBOUND",
                "CELL_RESERVATION",
                "OUTBOUND_ARM_TO_CELL",
                "LOCAL_PHYSICAL_FACT",
                "WMS_SYNC",
            ],
            "local_position_state": "LOCAL_PHYSICAL_COMPLETED" if local_physical_completed else "PENDING",
            "wms_sync_state": wms_sync_state,
            "business_completion_state": business_completion_state,
            "preserve_local_physical_fact": local_physical_completed,
            "next_object_admission_allowed": True,
            "legacy_plugin_entry_used": False,
            "effect_ports": {
                "pkg_binding": "wms.fulfillment.notify_pkg_binding@v1",
                "inventory_transaction": "wms.inventory.confirm_inbound@v1",
            },
        },
    }


def _source_arm_prefetch_capacity(payload: dict[str, Any]) -> int:
    manifest = payload.get("manifest")
    if not isinstance(manifest, dict):
        return 0
    raw_capacity = manifest.get("source_arm_prefetch_capacity", 0)
    try:
        capacity = int(raw_capacity)
    except (TypeError, ValueError):
        return 0
    return max(capacity, 0)


def _source_arm_prefetch_manifest_validation(payload: dict[str, Any], capacity: int) -> dict[str, Any]:
    if capacity <= 0:
        return {"allowed": True, "errors": []}
    manifest = payload.get("manifest")
    manifest = manifest if isinstance(manifest, dict) else {}
    errors: list[str] = []
    ecs_capabilities = manifest.get("ecs_capabilities")
    if not isinstance(ecs_capabilities, list) or "SOURCE_ARM_PREFETCH" not in ecs_capabilities:
        errors.append("ECS_SOURCE_ARM_PREFETCH_CAPABILITY_REQUIRED")
    try:
        prefetch_buffer_capacity = int(manifest.get("prefetch_buffer_capacity", 0))
    except (TypeError, ValueError):
        prefetch_buffer_capacity = 0
    if prefetch_buffer_capacity < capacity:
        errors.append("PREFETCH_BUFFER_CAPACITY_TOO_SMALL")
    try:
        prefetch_timeout_ms = int(manifest.get("prefetch_timeout_ms", 0))
    except (TypeError, ValueError):
        prefetch_timeout_ms = 0
    if prefetch_timeout_ms <= 0:
        errors.append("PREFETCH_TIMEOUT_REQUIRED")
    return {"allowed": not errors, "errors": errors}


@app.post("/api/wms/fulfillment/sorter-inbound-preview", summary="本机 Mock: 分拣机入库预览")
async def sorter_inbound_preview(payload: dict[str, Any]):
    """表达分拣机入库 join gate 与扫码平台预取互锁合同。"""

    expected_authorized_bin_ids = set(_string_list(payload, "expected_authorized_bin_ids"))
    actual_scanned_bin_id = str(payload.get("actual_scanned_bin_id") or "")
    target_bin_ready = payload.get("target_bin_position_state") == "AT_WORK_POSITION"
    target_cell_reservable = bool(payload.get("target_cell_reservable"))
    reservation_ready = payload.get("cell_reservation_state") == "RESERVED"
    waiting_deadline_declared = bool(payload.get("waiting_deadline_declared"))
    condition_results = {
        "AUTHORIZED_BIN_RESOLVED": actual_scanned_bin_id in expected_authorized_bin_ids,
        "TARGET_BIN_AT_WORK_POSITION": target_bin_ready,
        "TARGET_CELL_RESERVABLE": target_cell_reservable,
        "CELL_RESERVATION_RESERVED": reservation_ready,
        "WAITING_DEADLINE_DECLARED": waiting_deadline_declared,
    }
    missing_conditions = [name for name, passed in condition_results.items() if not passed]
    capacity = _source_arm_prefetch_capacity(payload)
    manifest_validation = _source_arm_prefetch_manifest_validation(payload, capacity)
    scanner_platform_free = payload.get("scanner_platform_state") == "FREE"
    can_pick_next_material = (capacity > 0 and manifest_validation["allowed"]) or scanner_platform_free
    allowed = not missing_conditions
    return {
        "code": 200,
        "data": {
            "environment": "LOCAL_MOCK_ONLY",
            "production_write_path": False,
            "request_id": payload.get("request_id", ""),
            "legacy_plugin_entry_used": False,
            "prefetch_policy": {
                "source_arm_prefetch_capacity": capacity,
                "can_pick_next_material": can_pick_next_material,
                "requires_scanner_platform_free": capacity == 0,
            },
            "manifest_validation": manifest_validation,
            "ordered_steps": [
                "STATION_ADMISSION",
                "WMS_CTU_BIN_INFEED",
                "SCAN1_AUTHORIZED_RESOLVE",
                "SCAN2_ROUTE_DECISION",
                "SCAN3_RETURN_OR_NG_ROUTE",
                "SOURCE_ARM_TO_SCANNER_PLATFORM",
                "CELL_RESERVATION",
                "SOUTH_ARM_DROP",
                "LOCAL_PHYSICAL_FACT",
                "WMS_SYNC",
            ],
            "join_gate": {
                "allowed": allowed,
                "condition_results": condition_results,
                "missing_conditions": missing_conditions,
            },
            "local_position_state": "LOCAL_PHYSICAL_COMPLETED" if allowed else "PENDING",
            "wms_sync_state": "READY_TO_SYNC" if allowed else "BLOCKED",
            "business_completion_state": "READY_TO_DROP" if allowed else "RECONCILING",
            "ng_route_state": "CLEAR" if allowed else "NG_OR_RUNTIME_HOLD",
            "runtime_hold_required": not allowed,
        },
    }


def _duplicate_int_values(values: list[int]) -> list[int]:
    seen: set[int] = set()
    duplicates: set[int] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


@app.post("/api/wms/fulfillment/ctu-batch-preview", summary="本机 Mock: CTU 父子批次预览")
async def ctu_batch_preview(payload: dict[str, Any]):
    """表达 CTU 父请求查询视图，父成功不能掩盖子项未收敛。"""

    raw_child_items = payload.get("child_items")
    child_items = raw_child_items if isinstance(raw_child_items, list) else []
    sequence_nos = [int(item.get("sequence_no", 0)) for item in child_items if isinstance(item, dict)]
    missing_resolved_placeholders = [
        str(item.get("placeholder_key") or "")
        for item in child_items
        if isinstance(item, dict) and not item.get("resolved_bin_id")
    ]
    failed_child_placeholders = [
        str(item.get("placeholder_key") or "")
        for item in child_items
        if isinstance(item, dict) and item.get("stage_status") != "COMPLETED"
    ]
    duplicate_sequence_nos = _duplicate_int_values(sequence_nos)
    has_child_issues = bool(missing_resolved_placeholders or failed_child_placeholders or duplicate_sequence_nos)
    parent_callback_state = str(payload.get("parent_callback_state") or "PENDING").upper()
    parent_business_completed = parent_callback_state == "SUCCESS" and not has_child_issues
    operator_summary_state = "COMPLETED" if parent_business_completed else "RECONCILING"
    return {
        "code": 200,
        "data": {
            "environment": "LOCAL_MOCK_ONLY",
            "production_write_path": False,
            "parent_request_id": payload.get("parent_request_id", ""),
            "parent_callback_state": parent_callback_state,
            "parent_business_completed": parent_business_completed,
            "parent_projection_state": operator_summary_state,
            "legacy_plugin_entry_used": False,
            "query_view": {
                "child_count": len(child_items),
                "missing_resolved_placeholders": missing_resolved_placeholders,
                "duplicate_sequence_nos": duplicate_sequence_nos,
                "failed_child_placeholders": failed_child_placeholders,
                "operator_summary_state": operator_summary_state,
            },
        },
    }


@app.post("/api/wms/reconciliation/snapshot", summary="本机 Mock: WMS 对账快照")
async def reconciliation_snapshot(payload: dict[str, Any]):
    """模拟 WmsReconciliationQueryPort 快照, 不产生生产写入副作用。"""

    scenario = str(payload.get("scenario") or "OK").upper()
    conflict_state = "OK"
    if scenario == "DUPLICATE_CALLBACK":
        conflict_state = "IDEMPOTENT_DUPLICATE"
    elif scenario != "OK":
        conflict_state = "RECONCILING"
    return {
        "code": 200,
        "data": {
            "environment": "LOCAL_MOCK_ONLY",
            "production_write_path": False,
            "scenario": scenario,
            "object_type": payload.get("object_type", ""),
            "object_key": payload.get("object_key", ""),
            "source_event_id": payload.get("source_event_id", ""),
            "source_version": payload.get("source_version", "mock-wms.v1"),
            "conflict_state": conflict_state,
            "requires_runtime_hold": conflict_state == "RECONCILING",
            "allowed_next_effect_scope": "OBJECT_ONLY",
        },
    }


@app.post("/api/wms/reconciliation/runtime-hold-release-preview", summary="本机 Mock: RuntimeHold 解除预览")
async def runtime_hold_release_preview(payload: dict[str, Any]):
    """表达 RuntimeHold scope-only release 合同，不实际解除任何生产 hold。"""

    allowed_scope = str(payload.get("allowed_next_effect_scope") or "OBJECT_ONLY").upper()
    requested_scope = str(payload.get("requested_release_scope") or allowed_scope).upper()
    released_effect_scopes_by_allowed_scope = {
        "OBJECT_ONLY": ["OBJECT"],
        "QUEUE_ONLY": ["QUEUE"],
        "DEVICE_ONLY": ["DEVICE"],
        "RESOURCE_ONLY": ["RESOURCE"],
        "WORKLINE": ["WORKLINE"],
    }
    released_effect_scopes = released_effect_scopes_by_allowed_scope.get(allowed_scope, ["OBJECT"])
    all_effect_scopes = ["OBJECT", "WORKLINE", "QUEUE", "DEVICE", "RESOURCE"]
    blocked_effect_scopes = [scope for scope in all_effect_scopes if scope not in released_effect_scopes]
    return {
        "code": 200,
        "data": {
            "environment": "LOCAL_MOCK_ONLY",
            "production_write_path": False,
            "hold_id": payload.get("hold_id", ""),
            "scope_type": payload.get("scope_type", ""),
            "scope_key": payload.get("scope_key", ""),
            "allowed_next_effect_scope": allowed_scope,
            "requested_release_scope": requested_scope,
            "released_effect_scopes": released_effect_scopes,
            "blocked_effect_scopes": blocked_effect_scopes,
            "requires_manual_review": requested_scope != allowed_scope,
        },
    }


@app.get("/api/wms/grn/{grn_id}")
async def get_grn(grn_id: str):
    if grn_id in MOCK_GRNS:
        return {"code": 200, "data": MOCK_GRNS[grn_id]}
    return Response(status_code=404, content='{"code": 404, "message": "GRN Not Found"}', media_type="application/json")


# --- 交易接口 (Inventory / Reservations) ---


def _inventory_items(
    *,
    sku: str,
    lot_no: str | None = None,
    warehouse_code: str | None = None,
    owner_code: str | None = None,
) -> list[dict[str, Any]]:
    if not sku:
        return []
    return [
        dict(item)
        for (item_sku, item_lot_no), item in MOCK_INVENTORY.items()
        if item_sku == sku
        and (lot_no is None or item_lot_no == lot_no)
        and (warehouse_code is None or item.get("warehouse_code") == warehouse_code)
        and (owner_code is None or item.get("owner_code") == owner_code)
    ]


def _static_effect_handler(operation_identity: str):
    async def handler(request: Request, background_tasks: BackgroundTasks):
        return await _submit_northbound_effect(
            request=request,
            background_tasks=background_tasks,
            operation_identity=operation_identity,
        )

    handler.__name__ = f"northbound_{operation_identity.replace('.', '_').replace('@', '_')}"
    handler.__wms_operation_identity__ = operation_identity
    return handler


def _typed_query_payload(request: Request, operation_identity: str) -> dict[str, Any]:
    operation = WMS_OPERATION_BY_IDENTITY[operation_identity]
    payload: dict[str, Any] = dict(request.path_params)
    for field_name in operation.request_model.model_fields:
        values = request.query_params.getlist(field_name)
        if values:
            payload[field_name] = values if len(values) > 1 else values[0]
    return payload


def _static_query_handler(operation_identity: str):
    async def handler(request: Request):
        operation = WMS_OPERATION_BY_IDENTITY[operation_identity]
        if request.method == "GET":
            raw_path = request.scope["path"]
            query_string = request.scope.get("query_string", b"")
            if query_string:
                raw_path = f"{raw_path}?{query_string.decode('ascii')}"
            body = await _request_body_or_none(request)
            if body is None:
                return Response(status_code=499)
            try:
                verify_status_hmac(request.headers, body, method=request.method, path=raw_path)
                northbound_hmac_replay_guard.consume(
                    credential_reference=request.headers["X-WMS-Credential-Reference"],
                    timestamp=request.headers["X-WMS-Timestamp"],
                    nonce=request.headers["X-WMS-Nonce"],
                )
            except NorthboundAuthError as exc:
                return JSONResponse(status_code=401, content={"code": exc.code})
            payload = _typed_query_payload(request, operation_identity)
        else:
            body = await _request_body_or_none(request)
            if body is None:
                return Response(status_code=499)
            try:
                payload = json.loads(body)
            except (UnicodeDecodeError, json.JSONDecodeError):
                return JSONResponse(status_code=422, content={"code": "INVALID_TYPED_REQUEST"})
        try:
            validated = operation.request_model.model_validate(payload).model_dump(mode="json")
            if operation_identity == "wms.inventory.query_inventory@v1":
                inventory_items = _inventory_items(
                    sku=validated["material_code"],
                    lot_no=validated.get("lot_no"),
                    warehouse_code=validated.get("warehouse_code"),
                    owner_code=validated.get("owner_code"),
                )
                result = {
                    "items": [
                        {
                            "material_code": item["sku"],
                            "available_quantity": str(item["available_qty"]),
                            "total_quantity": str(item["total_qty"]),
                            "reserved_quantity": str(item["reserved_qty"]),
                            "lot_no": item.get("lot_no"),
                        }
                        for item in inventory_items
                    ],
                    "next_cursor": None,
                    "source_version": "mock-inventory-v1",
                }
                result = operation.result_model.model_validate(result).model_dump(mode="json")
            else:
                result = build_typed_result(
                    operation_identity,
                    validated,
                    source_version=0,
                    completed_at=datetime.now(UTC).isoformat(),
                )
        except (TypeError, ValueError):
            return JSONResponse(status_code=422, content={"code": "INVALID_TYPED_REQUEST"})
        return JSONResponse(status_code=200, content=result)

    handler.__name__ = f"northbound_{operation_identity.replace('.', '_').replace('@', '_')}"
    handler.__wms_operation_identity__ = operation_identity
    return handler


def _register_frozen_operation_routes() -> None:
    """启动期从唯一 registry 一次性注册 35 条明确 route。"""

    for operation in WMS_OPERATIONS:
        handler_factory = _static_query_handler if operation.mode.value == "QUERY" else _static_effect_handler
        app.add_api_route(
            f"/api/wms{operation.path_template}",
            handler_factory(operation.identity),
            methods=[operation.http_method.value],
            name=f"northbound:{operation.identity}",
        )


_register_frozen_operation_routes()


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
        self.config = Config(app=app, host=host, port=port, log_level="info", access_log=False)

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
