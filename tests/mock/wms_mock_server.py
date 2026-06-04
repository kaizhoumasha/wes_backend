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

# 添加项目根目录到 sys.path
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
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
RACK_SLOT_CODES = ("A", "B", "C", "D")
RACK_PHYSICAL_LAYOUTS = {
    "RACK-001": {
        "bin_type": "6格箱",
        "bin_prefix": "BIN",
        "cell_indexes": SEVEN_INCH_BIN_CELL_INDEXES,
    },
    "RACK-3CELL-001": {
        "bin_type": "3格箱",
        "bin_prefix": "BIN-3CELL-001",
        "cell_indexes": THREE_CELL_BIN_CELL_INDEXES,
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

# ============================================
# FastAPI 应用
# ============================================

app = FastAPI(title="WMS Mock Server", description="模拟 WMS 主数据查询及库存操作接口", version="1.0.0")


@app.middleware("http")
async def fault_injection_middleware(request: Request, call_next):
    if fault_injection_state["next_delay"] > 0:
        await asyncio.sleep(fault_injection_state["next_delay"])
        fault_injection_state["next_delay"] = 0.0

    if fault_injection_state["next_status"] != 200 and not request.url.path.startswith("/debug"):
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


@app.post("/debug/simulate-failure", summary="注入 HTTP 故障")
async def simulate_failure(request: SimulateFailureRequest):
    """
    配置下一次外部调用（非 /debug）将返回指定的 HTTP 状态码并等待 delay 秒。
    这适用于触发 WES 端的 Circuit Breaker 和 WmsUnavailableError 测试。
    """
    fault_injection_state["next_status"] = request.status
    fault_injection_state["next_delay"] = request.delay
    return {"message": f"Next request will return {request.status} after {request.delay}s"}


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
    layout = RACK_PHYSICAL_LAYOUTS[rack_code]
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
        "active_bin_rack": {
            "rack_id": rack_code,
            "rack_code": rack_code,
            "rack_kind": rack_kind,
            "rack_type": rack_kind,
            "cells": cells,
        },
        "bin_mounts": bin_mounts,
    }
    if callback_type in RACK_STATUS_CALLBACK_TYPES and not any(
        str(callback_payload.get(field) or "").strip() for field in RACK_STATUS_FIELDS
    ):
        callback_payload["status"] = "SUCCESS"
        callback_payload["task_status"] = "SUCCESS"
        callback_payload["result"] = "SUCCESS"
    return callback_payload


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
        layout = RACK_PHYSICAL_LAYOUTS.get(requested_rack_code)
        if layout is not None and layout["bin_type"] == required_bin_type:
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


async def _report_rack_operation_arrived(payload: dict[str, Any]) -> None:
    callback_payload = _rack_operation_callback_payload(payload)
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
    if rack_id in MOCK_RACKS:
        return {"code": 200, "data": MOCK_RACKS[rack_id]}
    return Response(
        status_code=404, content='{"code": 404, "message": "Rack Not Found"}', media_type="application/json"
    )


@app.get("/api/wms/racks")
async def get_racks(type: str | None = None):
    data = list(MOCK_RACKS.values())
    if type:
        data = [r for r in data if r["rack_type"] == type]
    return {"code": 200, "data": data}


@app.post("/api/wms/rack-operation", summary="接收货架操作请求并回调 WES")
async def rack_operation(payload: dict[str, Any], background_tasks: BackgroundTasks):
    background_tasks.add_task(_report_rack_operation_arrived, dict(payload))
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
        logger.info("  - 模拟货架数: %d", len(MOCK_RACKS))
        server = Server(self.config)
        await server.serve()

    def run(self):
        asyncio.run(self.start())


if __name__ == "__main__":
    server = WmsMockServer()
    server.run()
