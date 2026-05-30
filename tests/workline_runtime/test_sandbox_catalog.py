from decimal import Decimal

from src.workline_runtime.sandbox_catalog import (
    mock_wms_inventory_seed,
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


def test_catalog_inventory_query_matches_happy_path_and_unknown_returns_empty() -> None:
    rows = query_sandbox_wms_inventory_rows(sku="CAP001", lot_no="LOT-A")
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
