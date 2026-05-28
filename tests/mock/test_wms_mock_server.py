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
