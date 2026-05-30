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

_MOCK_WMS_INVENTORY: dict[tuple[str, str], dict[str, Any]] = {
    ("CAP001", "LOT-A"): {
        "sku": "CAP001",
        "lot_no": "LOT-A",
        "warehouse_code": None,
        "owner_code": None,
        "total_qty": Decimal("50000"),
        "available_qty": Decimal("50000"),
        "reserved_qty": Decimal("0"),
    }
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
            field_name: int(value) if isinstance(value, Decimal) else value
            for field_name, value in row.items()
            if field_name not in {"warehouse_code", "owner_code"} or value is not None
        }
        for key, row in _MOCK_WMS_INVENTORY.items()
    }


def _matches_optional_dimension(row_value: Any, requested_value: str | None) -> bool:
    if requested_value is None:
        return True
    return row_value is None or row_value == requested_value


__all__ = [
    "mock_wms_inventory_seed",
    "mock_wms_materials_seed",
    "query_sandbox_wms_inventory_rows",
    "rough_sorter_scan_completed_payload",
]
