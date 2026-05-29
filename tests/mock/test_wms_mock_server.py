from fastapi.testclient import TestClient

from tests.mock import wms_mock_server


def test_wms_mock_release_reservation_matches_typed_port_contract() -> None:
    with TestClient(wms_mock_server.app) as client:
        response = client.delete("/api/wms/inventory/reserve/RSV-1")

    assert response.status_code == 200
    assert response.json() == {
        "code": 200,
        "data": {
            "reservation_key": "RSV-1",
            "released": True,
        },
    }


def test_wms_mock_locations_route_passes_ruff_safe_variable_path() -> None:
    with TestClient(wms_mock_server.app) as client:
        response = client.get("/api/wms/locations", params={"zone": "KITTING_AREA"})

    assert response.status_code == 200
    assert response.json()["data"] == [
        {
            "location_code": "KITTING_AREA_LOC_01",
            "zone_code": "KITTING_AREA",
            "location_type": "BUFFER",
            "status": "AVAILABLE",
        }
    ]


def test_wms_mock_inventory_query_matches_known_sku_and_lot_no() -> None:
    with TestClient(wms_mock_server.app) as client:
        response = client.post("/api/wms/inventory/query", json={"sku": "CAP001", "lot_no": "LOT-A"})

    assert response.status_code == 200
    assert response.json()["data"]["items"] == [
        {
            "sku": "CAP001",
            "lot_no": "LOT-A",
            "total_qty": 50000,
            "available_qty": 50000,
            "reserved_qty": 0,
        }
    ]


def test_wms_mock_inventory_query_returns_empty_items_for_unknown_sku_or_lot_no() -> None:
    with TestClient(wms_mock_server.app) as client:
        unknown_sku_response = client.post("/api/wms/inventory/query", json={"sku": "UNKNOWN", "lot_no": "LOT-A"})
        unknown_lot_response = client.get("/api/wms/inventory/query", params={"sku": "CAP001", "lot_no": "UNKNOWN"})

    assert unknown_sku_response.status_code == 200
    assert unknown_sku_response.json()["data"]["items"] == []
    assert unknown_lot_response.status_code == 200
    assert unknown_lot_response.json()["data"]["items"] == []
