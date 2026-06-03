from decimal import Decimal

from src.workline_runtime import sandbox_catalog
from src.workline_runtime.sandbox_catalog import (
    mock_wms_inventory_seed,
    mock_wms_materials_seed,
    query_sandbox_wms_inventory_rows,
    rough_sorter_scan_completed_payload,
)


def test_rough_sorter_scan_completed_payload_uses_catalog_happy_path() -> None:
    payload = rough_sorter_scan_completed_payload()

    assert payload == {
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


def test_catalog_payload_and_mock_seed_return_independent_copies() -> None:
    first_payload = rough_sorter_scan_completed_payload()
    first_payload["data"]["HHPN"] = "BROKEN"
    second_payload = rough_sorter_scan_completed_payload()

    first_seed = mock_wms_inventory_seed()
    first_seed[("CAP001", "LOT-A")]["sku"] = "BROKEN"
    second_seed = mock_wms_inventory_seed()

    assert second_payload["data"]["HHPN"] == "CAP001"
    assert second_seed[("CAP001", "LOT-A")]["sku"] == "CAP001"


def test_mock_wms_inventory_seed_rejects_fractional_decimal_quantity(monkeypatch) -> None:
    inventory = dict(sandbox_catalog._MOCK_WMS_INVENTORY)
    inventory[("FRACTIONAL", "LOT-F")] = {
        "sku": "FRACTIONAL",
        "lot_no": "LOT-F",
        "warehouse_code": None,
        "owner_code": None,
        "total_qty": Decimal("1.5"),
        "available_qty": Decimal("1.5"),
        "reserved_qty": Decimal("0"),
    }
    monkeypatch.setattr(sandbox_catalog, "_MOCK_WMS_INVENTORY", inventory)

    try:
        mock_wms_inventory_seed()
    except ValueError as exc:
        assert "库存数量必须是整数" in str(exc)
    else:
        raise AssertionError("mock_wms_inventory_seed() should reject fractional Decimal quantities")


def test_catalog_inventory_query_matches_happy_path_and_unknown_returns_empty() -> None:
    rows = query_sandbox_wms_inventory_rows(sku="CAP001", lot_no="LOT-A")
    resistor_rows = query_sandbox_wms_inventory_rows(sku="RES001", lot_no="LOT-R")
    unknown_sku_rows = query_sandbox_wms_inventory_rows(sku="UNKNOWN", lot_no="LOT-A")
    unknown_lot_rows = query_sandbox_wms_inventory_rows(sku="CAP001", lot_no="UNKNOWN")

    assert rows == [
        {
            "sku": "CAP001",
            "lot_no": "LOT-A",
            "warehouse_code": None,
            "owner_code": None,
            "total_qty": Decimal("50000"),
            "available_qty": Decimal("50000"),
            "reserved_qty": Decimal("0"),
        }
    ]
    assert resistor_rows == [
        {
            "sku": "RES001",
            "lot_no": "LOT-R",
            "warehouse_code": None,
            "owner_code": None,
            "total_qty": Decimal("30000"),
            "available_qty": Decimal("30000"),
            "reserved_qty": Decimal("0"),
        }
    ]
    assert unknown_sku_rows == []
    assert unknown_lot_rows == []


def test_catalog_inventory_query_echoes_requested_owner_dimensions() -> None:
    rows = query_sandbox_wms_inventory_rows(
        sku="CAP001",
        lot_no="LOT-A",
        warehouse_code="WH-SANDBOX",
        owner_code="OWNER-SANDBOX",
    )

    assert rows[0]["warehouse_code"] == "WH-SANDBOX"
    assert rows[0]["owner_code"] == "OWNER-SANDBOX"


def test_mock_catalog_exposes_multiple_materials_with_distinct_reel_measurements() -> None:
    materials = mock_wms_materials_seed()
    inventory = mock_wms_inventory_seed()

    assert {"CAP001", "RES001", "IC001", "LED001", "PCB001"} <= set(materials)
    assert ("RES001", "LOT-R") in inventory
    assert materials["RES001"]["standard_reel_diameter"] == 180.0
    assert materials["IC001"]["standard_reel_thickness"] == 24.0

    assert sandbox_catalog.mock_rough_sorter_reel_measurement("CAP001", "LOT-A") == {
        "reel_diameter": "178.0",
        "reel_thickness": "15.0",
        "measurement_result": "OK",
    }
    assert sandbox_catalog.mock_rough_sorter_reel_measurement("IC001", "LOT-I") == {
        "reel_diameter": "330.0",
        "reel_thickness": "24.0",
        "measurement_result": "OK",
    }


def test_mock_reel_measurement_rejects_unknown_material_or_inventory_pair() -> None:
    assert sandbox_catalog.mock_rough_sorter_reel_measurement("UNKNOWN", "LOT-X") == {
        "measurement_result": "NG",
        "measurement_error_code": "MATERIAL_NOT_SUPPORTED",
    }
    assert sandbox_catalog.mock_rough_sorter_reel_measurement("", "LOT-A") == {
        "measurement_result": "NG",
        "measurement_error_code": "MATERIAL_NOT_SUPPORTED",
    }
    assert sandbox_catalog.mock_rough_sorter_reel_measurement("IC001", "LOT-X") == {
        "measurement_result": "NG",
        "measurement_error_code": "MATERIAL_INVENTORY_NOT_ALLOWED",
    }
    assert sandbox_catalog.mock_rough_sorter_reel_measurement("CAP001", "") == {
        "measurement_result": "NG",
        "measurement_error_code": "MATERIAL_INVENTORY_NOT_ALLOWED",
    }
