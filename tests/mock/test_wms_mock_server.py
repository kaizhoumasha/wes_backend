from pathlib import Path
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from src.workline_runtime.sandbox_catalog import mock_wms_inventory_seed, rough_sorter_scan_completed_payload
from tests.mock import wms_mock_server


def test_wms_mock_loads_shared_catalog_without_importing_runtime_package() -> None:
    source = Path(wms_mock_server.__file__).read_text()

    assert "from src.workline_runtime.sandbox_catalog import" not in source
    assert "spec_from_file_location" in source


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
    payload_data = rough_sorter_scan_completed_payload()["data"]
    inventory = mock_wms_inventory_seed()[("CAP001", "LOT-A")]
    with TestClient(wms_mock_server.app) as client:
        response = client.post(
            "/api/wms/inventory/query",
            json={"sku": payload_data["HHPN"], "lot_no": payload_data["LotCode"]},
        )

    assert response.status_code == 200
    assert response.json()["data"]["items"] == [inventory]


def test_wms_mock_inventory_query_matches_additional_catalog_products() -> None:
    inventory = mock_wms_inventory_seed()
    with TestClient(wms_mock_server.app) as client:
        resistor_response = client.post(
            "/api/wms/inventory/query",
            json={"sku": "RES001", "lot_no": "LOT-R"},
        )
        ic_response = client.get(
            "/api/wms/inventory/query",
            params={"sku": "IC001", "lot_no": "LOT-I"},
        )

    assert resistor_response.status_code == 200
    assert resistor_response.json()["data"]["items"] == [inventory[("RES001", "LOT-R")]]
    assert ic_response.status_code == 200
    assert ic_response.json()["data"]["items"] == [inventory[("IC001", "LOT-I")]]


def test_wms_mock_inventory_query_returns_empty_items_for_unknown_sku_or_lot_no() -> None:
    payload_data = rough_sorter_scan_completed_payload()["data"]
    with TestClient(wms_mock_server.app) as client:
        unknown_sku_response = client.post(
            "/api/wms/inventory/query",
            json={"sku": "UNKNOWN", "lot_no": payload_data["LotCode"]},
        )
        unknown_lot_response = client.get(
            "/api/wms/inventory/query",
            params={"sku": payload_data["HHPN"], "lot_no": "UNKNOWN"},
        )

    assert unknown_sku_response.status_code == 200
    assert unknown_sku_response.json()["data"]["items"] == []
    assert unknown_lot_response.status_code == 200
    assert unknown_lot_response.json()["data"]["items"] == []


def test_wms_mock_rack_operation_builds_wes_external_callback(monkeypatch) -> None:
    mock_post_callback = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(wms_mock_server, "_post_callback", mock_post_callback)
    with TestClient(wms_mock_server.app) as client:
        response = client.post(
            "/api/wms/rack-operation",
            json={
                "request_id": "rack-operation:op-001:1:ALLOCATE_AND_MOVE_RACK",
                "dispatch_key": "rack-operation:op-001:1:ALLOCATE_AND_MOVE_RACK",
                "callback_type": "WMS_RACK_ARRIVED",
                "operation_key": "op-001",
                "operation_type": "REPLACE_CLASSIFIER_WORK_RACK",
                "sequence_no": 1,
                "task_type": "ALLOCATE_AND_MOVE_RACK",
                "workline_code": "WL-ROUGH-SORTER-TEST",
                "rack_kind": "SINGLE_LAYER",
                "target_position_code": "SINGLE_LAYER_A",
                "trace_id": "trace-rack-001",
            },
        )

    assert response.status_code == 200
    assert response.json()["data"]["accepted"] is True
    mock_post_callback.assert_awaited_once()
    callback_payload = mock_post_callback.await_args.args[1]
    assert callback_payload["callback_type"] == "WMS_RACK_ARRIVED"
    assert callback_payload["dispatch_key"] == "rack-operation:op-001:1:ALLOCATE_AND_MOVE_RACK"
    assert callback_payload["source_event_id"].startswith("wms-mock:rack-operation:")
    assert callback_payload["source_system"] == "WMS"
    assert callback_payload["active_bin_rack"]["rack_code"] == "RACK-001"
    assert {cell["rack_slot_code"] for cell in callback_payload["active_bin_rack"]["cells"]} == {"A", "B", "C", "D"}
    assert len(callback_payload["active_bin_rack"]["cells"]) == 24
    assert {cell["bin_type"] for cell in callback_payload["active_bin_rack"]["cells"]} == {"6格箱"}
    assert {mount["rack_slot_code"] for mount in callback_payload["bin_mounts"]} == {"A", "B", "C", "D"}
    assert callback_payload["bin_mounts"][0]["bin_code"] == "BIN-001"


def test_wms_mock_rack_operation_source_event_id_keeps_wes_idempotency_key_short() -> None:
    dispatch_key = (
        "rack-operation:external:smt_rack_bin:rough-sorter-mock-scan-1780455233:RACK_OPERATION:1:ALLOCATE_AND_MOVE_RACK"
    )

    callback_payload = wms_mock_server._rack_operation_callback_payload(
        {
            "request_id": dispatch_key,
            "dispatch_key": dispatch_key,
            "callback_type": "WMS_RACK_ARRIVED",
            "operation_key": "external:smt_rack_bin:rough-sorter-mock-scan-1780455233:RACK_OPERATION",
            "trace_id": "rough-sorter-mock-scan-1780455233",
        }
    )

    idempotency_key = (
        f"external_http:{callback_payload['callback_type']}:{callback_payload['trace_id']}:"
        f"source_event:{callback_payload['source_event_id']}"
    )
    assert len(callback_payload["source_event_id"]) <= 200
    assert len(idempotency_key) <= 200


def test_wms_mock_rack_operation_task_result_includes_required_status() -> None:
    callback_payload = wms_mock_server._rack_operation_callback_payload(
        {
            "request_id": "rack-operation:op-002:2:MOVE_RACK",
            "dispatch_key": "rack-operation:op-002:2:MOVE_RACK",
            "callback_type": "WMS_RACK_TASK_RESULT",
            "operation_key": "op-002",
            "sequence_no": 2,
            "task_type": "MOVE_RACK",
            "workline_code": "WL-ROUGH-SORTER-TEST",
            "trace_id": "trace-rack-002",
        }
    )

    assert callback_payload["callback_type"] == "WMS_RACK_TASK_RESULT"
    assert callback_payload["status"] == "SUCCESS"
    assert callback_payload["task_status"] == "SUCCESS"
    assert callback_payload["result"] == "SUCCESS"
