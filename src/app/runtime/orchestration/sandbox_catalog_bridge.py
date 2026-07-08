# 旧 runtime 桥接实现:src.workline_runtime.sandbox_catalog 的门面副本
# 旧 runtime 入口删除后,本桥接承载对应正式边界。

"""SANDBOX / MOCK 运行所需的确定性样例 catalog。"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any

_ROUGH_SORTER_SCAN_COMPLETED_PAYLOAD: dict[str, Any] = {
    "data": {
        "location": "ARM01",
        "HHPN": "CAP001",
        "MfrPN": "V0001-CAP-0402",
        "Qty": "100",
        "DateCode": "20260409",
        "LotCode": "LOT-A",
        "PkgID": "PKG-CAP001-LOT-A-001",
    }
}

_MOCK_WMS_MATERIALS: dict[str, dict[str, Any]] = {
    "CAP001": {
        "material_id": "CAP001",
        "material_name": "电容 0402",
        "vendor": "V0001",
        "standard_dims": "7inch",
        "standard_reel_diameter": 178.0,
        "standard_thickness": 15.0,
        "standard_reel_thickness": 15.0,
        "is_msd": False,
        "is_high_value": False,
        "is_precious": False,
        "is_pcb": False,
        "is_irregular": False,
        "material_type": "ELECTRONIC",
        "lc_cycle": 30,
        "floor_life": 168,
    },
    "RES001": {
        "material_id": "RES001",
        "material_name": "电阻 0603",
        "vendor": "V0002",
        "standard_dims": "7inch",
        "standard_reel_diameter": 180.0,
        "standard_thickness": 12.0,
        "standard_reel_thickness": 12.0,
        "is_msd": False,
        "is_high_value": False,
        "is_precious": False,
        "is_pcb": False,
        "is_irregular": False,
        "material_type": "ELECTRONIC",
        "lc_cycle": 30,
        "floor_life": 168,
    },
    "IC001": {
        "material_id": "IC001",
        "material_name": "IC QFN",
        "vendor": "V0003",
        "standard_dims": "13inch",
        "standard_reel_diameter": 330.0,
        "standard_thickness": 24.0,
        "standard_reel_thickness": 24.0,
        "is_msd": True,
        "is_high_value": True,
        "is_precious": False,
        "is_pcb": False,
        "is_irregular": False,
        "material_type": "ELECTRONIC",
        "lc_cycle": 14,
        "floor_life": 72,
    },
    "LED001": {
        "material_id": "LED001",
        "material_name": "LED 2835",
        "vendor": "V0004",
        "standard_dims": "7inch",
        "standard_reel_diameter": 178.0,
        "standard_thickness": 18.0,
        "standard_reel_thickness": 18.0,
        "is_msd": False,
        "is_high_value": False,
        "is_precious": False,
        "is_pcb": False,
        "is_irregular": False,
        "material_type": "ELECTRONIC",
        "lc_cycle": 21,
        "floor_life": 120,
    },
    "PCB001": {
        "material_id": "PCB001",
        "material_name": "小型 PCB 载盘",
        "vendor": "V0005",
        "standard_dims": "tray",
        "standard_reel_diameter": 260.0,
        "standard_thickness": 30.0,
        "standard_reel_thickness": 30.0,
        "is_msd": False,
        "is_high_value": True,
        "is_precious": False,
        "is_pcb": True,
        "is_irregular": True,
        "material_type": "PCB",
        "lc_cycle": 7,
        "floor_life": 48,
    },
}

_MOCK_WMS_INVENTORY: dict[tuple[str, str], dict[str, Any]] = {
    ("CAP001", "LOT-A"): {
        "sku": "CAP001",
        "lot_no": "LOT-A",
        "warehouse_code": None,
        "owner_code": None,
        "total_qty": Decimal("50000"),
        "available_qty": Decimal("50000"),
        "reserved_qty": Decimal("0"),
    },
    ("RES001", "LOT-R"): {
        "sku": "RES001",
        "lot_no": "LOT-R",
        "warehouse_code": None,
        "owner_code": None,
        "total_qty": Decimal("30000"),
        "available_qty": Decimal("30000"),
        "reserved_qty": Decimal("0"),
    },
    ("IC001", "LOT-I"): {
        "sku": "IC001",
        "lot_no": "LOT-I",
        "warehouse_code": None,
        "owner_code": None,
        "total_qty": Decimal("12000"),
        "available_qty": Decimal("12000"),
        "reserved_qty": Decimal("0"),
    },
    ("LED001", "LOT-L"): {
        "sku": "LED001",
        "lot_no": "LOT-L",
        "warehouse_code": None,
        "owner_code": None,
        "total_qty": Decimal("24000"),
        "available_qty": Decimal("24000"),
        "reserved_qty": Decimal("0"),
    },
    ("PCB001", "LOT-P"): {
        "sku": "PCB001",
        "lot_no": "LOT-P",
        "warehouse_code": None,
        "owner_code": None,
        "total_qty": Decimal("6000"),
        "available_qty": Decimal("6000"),
        "reserved_qty": Decimal("0"),
    },
}


def rough_sorter_scan_completed_payload() -> dict[str, Any]:
    """返回粗分机 SANDBOX 默认扫码事件 payload 副本。"""

    return deepcopy(_ROUGH_SORTER_SCAN_COMPLETED_PAYLOAD)


def query_sandbox_wms_inventory_rows(
    *,
    sku: str,
    lot_no: str | None = None,
    warehouse_code: str | None = None,
    owner_code: str | None = None,
) -> list[dict[str, Any]]:
    """按 SANDBOX catalog 查询 WMS 库存行，未命中时返回空列表。"""

    if not sku:
        return []

    rows: list[dict[str, Any]] = []
    for (row_sku, row_lot_no), row in _MOCK_WMS_INVENTORY.items():
        if row_sku != sku:
            continue
        if lot_no is not None and row_lot_no != lot_no:
            continue
        if not _matches_optional_dimension(row.get("warehouse_code"), warehouse_code):
            continue
        if not _matches_optional_dimension(row.get("owner_code"), owner_code):
            continue

        copied = dict(row)
        copied["warehouse_code"] = warehouse_code or copied.get("warehouse_code")
        copied["owner_code"] = owner_code or copied.get("owner_code")
        rows.append(copied)
    return rows


def mock_wms_materials_seed() -> dict[str, dict[str, Any]]:
    """返回外部 mock WMS 使用的物料 seed 副本。"""

    return deepcopy(_MOCK_WMS_MATERIALS)


def mock_wms_inventory_seed() -> dict[tuple[str, str], dict[str, Any]]:
    """返回外部 mock WMS 使用的库存 seed 副本，数量保持 JSON 友好的数字类型。"""

    return {
        key: {
            field_name: _json_ready_inventory_value(value) if isinstance(value, Decimal) else value
            for field_name, value in row.items()
            if field_name not in {"warehouse_code", "owner_code"} or value is not None
        }
        for key, row in _MOCK_WMS_INVENTORY.items()
    }


def mock_rough_sorter_reel_measurement(sku: str | None, lot_no: str | None = None) -> dict[str, str]:
    """按共享 mock catalog 和 WMS 库存返回粗分机测量值。"""

    material_sku = "CAP001" if sku is None else sku
    material_lot_no = "LOT-A" if lot_no is None else lot_no
    material = _MOCK_WMS_MATERIALS.get(material_sku)
    if material is None:
        return {
            "measurement_result": "NG",
            "measurement_error_code": "MATERIAL_NOT_SUPPORTED",
        }
    if (material_sku, material_lot_no) not in _MOCK_WMS_INVENTORY:
        return {
            "measurement_result": "NG",
            "measurement_error_code": "MATERIAL_INVENTORY_NOT_ALLOWED",
        }
    return {
        "reel_diameter": str(float(material["standard_reel_diameter"])),
        "reel_thickness": str(float(material["standard_reel_thickness"])),
        "measurement_result": "OK",
    }


def _json_ready_inventory_value(value: Decimal) -> int:
    if value != value.to_integral_value():
        raise ValueError(f"Mock WMS 库存数量必须是整数: {value}")
    return int(value)


def _matches_optional_dimension(row_value: Any, requested_value: str | None) -> bool:
    if requested_value is None:
        return True
    return row_value is None or row_value == requested_value


__all__ = [
    "mock_rough_sorter_reel_measurement",
    "mock_wms_inventory_seed",
    "mock_wms_materials_seed",
    "query_sandbox_wms_inventory_rows",
    "rough_sorter_scan_completed_payload",
]
