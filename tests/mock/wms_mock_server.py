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
import logging
import os
import sys
from decimal import Decimal

# 添加项目根目录到 sys.path
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel
from uvicorn import Config, Server

project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ============================================
# 种子数据 (Seed Data)
# ============================================

MOCK_MATERIALS = {
    "CAP001": {
        "material_id": "CAP001",
        "material_name": "电容 0402",
        "vendor": "V0001",
        "standard_dims": "7inch",
        "standard_thickness": 15.0,
        "is_msd": False,
        "is_high_value": False,
        "is_precious": False,
        "is_pcb": False,
        "is_irregular": False,
        "material_type": "ELECTRONIC",
        "lc_cycle": 30,
        "floor_life": 168,
    }
}

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
    }
}

MOCK_INVENTORY = {
    ("CAP001", "LOT-A"): {
        "sku": "CAP001",
        "lot_no": "LOT-A",
        "total_qty": 50000,
        "available_qty": 50000,
        "reserved_qty": 0,
    }
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
