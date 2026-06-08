import asyncio
from pathlib import Path
from time import monotonic
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from src.workline_runtime.sandbox_catalog import mock_wms_inventory_seed, rough_sorter_scan_completed_payload
from tests.mock import wms_mock_server


@pytest.fixture(autouse=True)
def reset_wms_mock_state() -> None:
    wms_mock_server.reset_mock_wms_state()


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


def test_wms_mock_racks_route_returns_stateful_six_and_three_cell_pool() -> None:
    with TestClient(wms_mock_server.app) as client:
        response = client.get("/api/wms/racks", params={"type": "SINGLE_LAYER"})

    assert response.status_code == 200
    racks = response.json()["data"]
    six_cell_racks = [rack for rack in racks if rack["layout_code"] == "SIX_CELL"]
    three_cell_racks = [rack for rack in racks if rack["layout_code"] == "THREE_CELL"]
    mixed_racks = [rack for rack in racks if rack["layout_code"] == "MIXED"]
    assert len(six_cell_racks) >= 6
    assert len(three_cell_racks) >= 4
    assert "RACK-001" in {rack["rack_id"] for rack in mixed_racks}
    assert "RACK-3CELL-001" in {rack["rack_id"] for rack in three_cell_racks}
    for rack in racks:
        assert {
            "rack_id",
            "rack_type",
            "status",
            "current_location",
            "layout_code",
            "bin_type",
            "active_position_code",
            "allocated_operation_key",
        } <= rack.keys()


def test_wms_mock_debug_reset_restores_rack_state_and_clears_fault_injection() -> None:
    wms_mock_server.mock_wms_state.rack_pool["RACK-001"]["status"] = "ALLOCATED"
    wms_mock_server.mock_wms_state.rack_pool["RACK-001"]["active_position_code"] = "SINGLE_LAYER_A"
    wms_mock_server.mock_wms_state.work_positions["SINGLE_LAYER_A"] = "RACK-001"
    wms_mock_server.mock_wms_state.recent_operations.append({"operation_key": "op-mutated"})
    wms_mock_server.fault_injection_state["next_status"] = 503
    wms_mock_server.fault_injection_state["next_delay"] = 1.5

    with TestClient(wms_mock_server.app) as client:
        response = client.post("/debug/reset")

    assert response.status_code == 200
    assert response.json()["data"]["reset"] is True
    rack = wms_mock_server.mock_wms_state.rack_pool["RACK-001"]
    assert rack["status"] == "AVAILABLE"
    assert rack["active_position_code"] is None
    assert rack["allocated_operation_key"] is None
    assert wms_mock_server.mock_wms_state.work_positions["SINGLE_LAYER_A"] is None
    assert wms_mock_server.mock_wms_state.recent_operations == []
    assert wms_mock_server.fault_injection_state == {"next_status": 200, "next_delay": 0.0}


def test_wms_mock_debug_reset_bypasses_pending_fault_injection_delay() -> None:
    wms_mock_server.fault_injection_state["next_status"] = 503
    wms_mock_server.fault_injection_state["next_delay"] = 1.5

    started_at = monotonic()
    with TestClient(wms_mock_server.app) as client:
        response = client.post("/debug/reset")
    elapsed = monotonic() - started_at

    assert response.status_code == 200
    assert elapsed < 0.5
    assert wms_mock_server.fault_injection_state == {"next_status": 200, "next_delay": 0.0}


def test_wms_mock_rack_operation_ignores_unknown_requested_rack_code() -> None:
    callback_payload = wms_mock_server._rack_operation_callback_payload(
        {
            "request_id": "rack-operation:op-unknown-rack:1:ALLOCATE_AND_MOVE_RACK",
            "dispatch_key": "rack-operation:op-unknown-rack:1:ALLOCATE_AND_MOVE_RACK",
            "callback_type": "WMS_RACK_ARRIVED",
            "operation_key": "op-unknown-rack",
            "task_type": "ALLOCATE_AND_MOVE_RACK",
            "rack_code": "RACK-DOES-NOT-EXIST",
        }
    )

    assert callback_payload["rack_code"] == "RACK-001"


def test_wms_mock_active_bin_rack_builder_does_not_read_or_mutate_state() -> None:
    removed_rack = wms_mock_server.mock_wms_state.rack_pool.pop("RACK-6CELL-001")

    payload = wms_mock_server.build_active_bin_rack_payload("RACK-6CELL-001")

    assert payload["active_bin_rack"]["rack_code"] == "RACK-6CELL-001"
    assert {cell["bin_type"] for cell in payload["active_bin_rack"]["cells"]} == {"6格箱"}
    assert "RACK-6CELL-001" not in wms_mock_server.mock_wms_state.rack_pool
    wms_mock_server.mock_wms_state.rack_pool["RACK-6CELL-001"] = removed_rack


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
    assert len(callback_payload["active_bin_rack"]["cells"]) == 18
    assert {cell["bin_type"] for cell in callback_payload["active_bin_rack"]["cells"]} == {"6格箱", "3格箱"}
    assert {
        cell["bin_cell_index"] for cell in callback_payload["active_bin_rack"]["cells"] if cell["rack_slot_code"] == "A"
    } == {"1", "2", "3", "4", "5", "6"}
    assert {
        cell["bin_cell_index"] for cell in callback_payload["active_bin_rack"]["cells"] if cell["rack_slot_code"] == "C"
    } == {"1", "2", "7"}
    assert {mount["rack_slot_code"] for mount in callback_payload["bin_mounts"]} == {"A", "B", "C", "D"}
    assert callback_payload["bin_mounts"][0]["bin_code"] == "BIN-001"


def _rack_allocate_payload(
    operation_key: str,
    material: dict[str, str],
    target_position_code: str = "SINGLE_LAYER_A",
) -> dict[str, object]:
    return {
        "request_id": f"rack-operation:{operation_key}:1:ALLOCATE_AND_MOVE_RACK",
        "dispatch_key": f"rack-operation:{operation_key}:1:ALLOCATE_AND_MOVE_RACK",
        "callback_type": "WMS_RACK_ARRIVED",
        "operation_key": operation_key,
        "operation_type": "REPLACE_CLASSIFIER_WORK_RACK",
        "sequence_no": 1,
        "task_type": "ALLOCATE_AND_MOVE_RACK",
        "workline_code": "WL-ROUGH-SORTER-TEST",
        "rack_kind": "SINGLE_LAYER",
        "target_position_code": target_position_code,
        "trace_id": f"trace-{operation_key}",
        "material": material,
    }


def _rack_replace_payload(operation_key: str) -> dict[str, object]:
    payload = _rack_allocate_payload(operation_key, THIRTEEN_INCH_MATERIAL)
    payload.pop("task_type")
    payload.pop("sequence_no")
    payload["rack_tasks"] = [
        {
            "sequence_no": 1,
            "task_type": "MOVE_OUT_ACTIVE_RACK",
            "rack_kind": "SINGLE_LAYER",
            "rack_code": "RACK-001",
            "source_position_code": "SINGLE_LAYER_A",
            "target_position_role": "SMT_EMPTY_RACK_AREA",
        },
        {
            "sequence_no": 2,
            "task_type": "ALLOCATE_AND_MOVE_RACK",
            "rack_kind": "SINGLE_LAYER",
            "target_position_code": "SINGLE_LAYER_A",
            "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
        },
    ]
    return payload


SEVEN_INCH_MATERIAL = {
    "HHPN": "RES001",
    "LotCode": "LOT-R",
    "DateCode": "20260407",
    "PkgID": "PKG-RES001-LOT-R-001",
}

THIRTEEN_INCH_MATERIAL = {
    "HHPN": "IC001",
    "LotCode": "LOT-I",
    "DateCode": "20260413",
    "PkgID": "PKG-IC001-LOT-I-001",
}


def _assert_failure_callback_contract(
    payload: dict[str, object],
    *,
    dispatch_key: str,
    operation_key: str,
    reason_code: str,
    reason_message: str | None = None,
) -> None:
    assert payload["callback_type"] == "WMS_RACK_EXCHANGE_FAILED"
    assert payload["dispatch_key"] == dispatch_key
    assert payload["request_id"] == dispatch_key
    assert payload["operation_key"] == operation_key
    assert payload["status"] == "FAILED"
    assert payload["task_status"] == "FAILED"
    assert payload["result"] == "FAILED"
    assert payload["reason_code"] == reason_code
    assert payload["error_code"] == reason_code
    if reason_message is not None:
        assert payload["reason_message"] == reason_message
        assert payload["error_message"] == reason_message
    else:
        assert isinstance(payload["reason_message"], str)
        assert payload["reason_message"]
        assert payload["error_message"] == payload["reason_message"]


def test_wms_mock_rack_operation_allocates_seven_inch_six_cell_rack_and_marks_unavailable(monkeypatch) -> None:
    mock_post_callback = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(wms_mock_server, "_post_callback", mock_post_callback)

    with TestClient(wms_mock_server.app) as client:
        response = client.post(
            "/api/wms/rack-operation",
            json=_rack_allocate_payload("op-7inch-allocated", SEVEN_INCH_MATERIAL),
        )

    assert response.status_code == 200
    callback_payload = mock_post_callback.await_args.args[1]
    assert callback_payload["callback_type"] == "WMS_RACK_ARRIVED"
    assert callback_payload["rack_code"] == "RACK-001"
    assert {cell["bin_type"] for cell in callback_payload["active_bin_rack"]["cells"]} == {"6格箱", "3格箱"}
    allocated_rack = wms_mock_server.mock_wms_state.rack_pool["RACK-001"]
    assert allocated_rack["status"] == "ACTIVE"
    assert allocated_rack["active_position_code"] == "SINGLE_LAYER_A"
    assert allocated_rack["allocated_operation_key"] == "op-7inch-allocated"
    assert wms_mock_server.mock_wms_state.recent_operations[-1]["rack_code"] == "RACK-001"


def test_wms_mock_rack_operation_allocates_13inch_three_cell_callback_cells(monkeypatch) -> None:
    mock_post_callback = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(wms_mock_server, "_post_callback", mock_post_callback)

    with TestClient(wms_mock_server.app) as client:
        response = client.post(
            "/api/wms/rack-operation",
            json=_rack_allocate_payload("op-13inch-allocated", THIRTEEN_INCH_MATERIAL),
        )

    assert response.status_code == 200
    callback_payload = mock_post_callback.await_args.args[1]
    cells = callback_payload["active_bin_rack"]["cells"]
    assert callback_payload["rack_code"] == "RACK-3CELL-001"
    assert callback_payload["active_bin_rack"]["rack_code"] == "RACK-3CELL-001"
    assert {cell["bin_cell_index"] for cell in cells if cell["rack_slot_code"] == "A"} == {"1", "2", "7"}
    assert {cell["capacity_depth_mm"] for cell in cells if cell["bin_cell_index"] == "7"} == {80.0}
    assert wms_mock_server.mock_wms_state.rack_pool["RACK-3CELL-001"]["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_wms_mock_state_allocates_distinct_three_cell_racks_until_pool_exhaustion() -> None:
    callback_payloads = []
    for index in range(1, 5):
        callback_payloads.append(
            await wms_mock_server.mock_wms_state.allocate_rack_for_payload(
                _rack_allocate_payload(f"op-13inch-{index}", THIRTEEN_INCH_MATERIAL)
            )
        )
        wms_mock_server.mock_wms_state.work_positions["SINGLE_LAYER_A"] = None

    exhausted_payload = await wms_mock_server.mock_wms_state.allocate_rack_for_payload(
        _rack_allocate_payload("op-13inch-exhausted-natural", THIRTEEN_INCH_MATERIAL)
    )

    assert [payload["rack_code"] for payload in callback_payloads] == [
        "RACK-3CELL-001",
        "RACK-3CELL-002",
        "RACK-3CELL-003",
        "RACK-3CELL-004",
    ]
    assert exhausted_payload["callback_type"] == "WMS_RACK_EXCHANGE_FAILED"
    assert exhausted_payload["reason_code"] == "NO_AVAILABLE_RACK"
    assert all(
        wms_mock_server.mock_wms_state.rack_pool[f"RACK-3CELL-{index:03d}"]["status"] == "ACTIVE"
        for index in range(1, 5)
    )


def test_wms_mock_rack_operation_exhausted_pool_uses_failure_callback_contract(monkeypatch) -> None:
    mock_post_callback = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(wms_mock_server, "_post_callback", mock_post_callback)
    for rack in wms_mock_server.mock_wms_state.rack_pool.values():
        if rack["bin_type"] == "3格箱":
            rack["status"] = "ALLOCATED"

    with TestClient(wms_mock_server.app) as client:
        response = client.post(
            "/api/wms/rack-operation",
            json=_rack_allocate_payload("op-13inch-exhausted", THIRTEEN_INCH_MATERIAL),
        )

    assert response.status_code == 200
    assert response.json()["data"]["accepted"] is True
    failure_payload = mock_post_callback.await_args.args[1]
    _assert_failure_callback_contract(
        failure_payload,
        dispatch_key="rack-operation:op-13inch-exhausted:1:ALLOCATE_AND_MOVE_RACK",
        operation_key="op-13inch-exhausted",
        reason_code="NO_AVAILABLE_RACK",
    )
    assert "3格箱" in failure_payload["reason_message"]


def test_wms_mock_rack_operation_occupied_position_uses_failure_callback_contract(monkeypatch) -> None:
    mock_post_callback = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(wms_mock_server, "_post_callback", mock_post_callback)
    wms_mock_server.mock_wms_state.work_positions["SINGLE_LAYER_A"] = "RACK-001"
    wms_mock_server.mock_wms_state.rack_pool["RACK-001"]["status"] = "ALLOCATED"

    with TestClient(wms_mock_server.app) as client:
        response = client.post(
            "/api/wms/rack-operation",
            json=_rack_allocate_payload("op-position-occupied", SEVEN_INCH_MATERIAL),
        )

    assert response.status_code == 200
    assert response.json()["data"]["accepted"] is True
    failure_payload = mock_post_callback.await_args.args[1]
    _assert_failure_callback_contract(
        failure_payload,
        dispatch_key="rack-operation:op-position-occupied:1:ALLOCATE_AND_MOVE_RACK",
        operation_key="op-position-occupied",
        reason_code="TARGET_POSITION_OCCUPIED",
        reason_message="目标工位 SINGLE_LAYER_A 已有活动货架 RACK-001",
    )


def test_wms_mock_rack_operation_requested_rack_layout_mismatch_uses_failure_callback_contract(monkeypatch) -> None:
    mock_post_callback = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(wms_mock_server, "_post_callback", mock_post_callback)
    payload = _rack_allocate_payload("op-layout-mismatch", THIRTEEN_INCH_MATERIAL)
    payload["rack_code"] = "RACK-6CELL-001"

    with TestClient(wms_mock_server.app) as client:
        response = client.post("/api/wms/rack-operation", json=payload)

    assert response.status_code == 200
    assert response.json()["data"]["accepted"] is True
    failure_payload = mock_post_callback.await_args.args[1]
    _assert_failure_callback_contract(
        failure_payload,
        dispatch_key="rack-operation:op-layout-mismatch:1:ALLOCATE_AND_MOVE_RACK",
        operation_key="op-layout-mismatch",
        reason_code="RACK_LAYOUT_MISMATCH",
        reason_message="指定货架 RACK-6CELL-001 不匹配 3格箱",
    )


def test_wms_mock_rack_operation_honors_requested_matching_available_rack(monkeypatch) -> None:
    mock_post_callback = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(wms_mock_server, "_post_callback", mock_post_callback)
    payload = _rack_allocate_payload("op-requested-rack", SEVEN_INCH_MATERIAL)
    payload["rack_code"] = "RACK-6CELL-003"

    with TestClient(wms_mock_server.app) as client:
        response = client.post("/api/wms/rack-operation", json=payload)

    assert response.status_code == 200
    callback_payload = mock_post_callback.await_args.args[1]
    assert callback_payload["callback_type"] == "WMS_RACK_ARRIVED"
    assert callback_payload["rack_code"] == "RACK-6CELL-003"
    assert wms_mock_server.mock_wms_state.rack_pool["RACK-6CELL-003"]["status"] == "ACTIVE"
    assert wms_mock_server.mock_wms_state.rack_pool["RACK-001"]["status"] == "AVAILABLE"


def test_wms_mock_single_task_rack_operation_preserves_request_dispatch_key(monkeypatch) -> None:
    mock_post_callback = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(wms_mock_server, "_post_callback", mock_post_callback)
    payload = _rack_allocate_payload("op-preserve-dispatch", SEVEN_INCH_MATERIAL)
    payload["dispatch_key"] = "custom-dispatch-key"
    payload["request_id"] = "custom-request-id"

    with TestClient(wms_mock_server.app) as client:
        response = client.post("/api/wms/rack-operation", json=payload)

    assert response.status_code == 200
    callback_payload = mock_post_callback.await_args.args[1]
    assert callback_payload["callback_type"] == "WMS_RACK_ARRIVED"
    assert callback_payload["dispatch_key"] == "custom-dispatch-key"
    assert callback_payload["request_id"] == "custom-request-id"


def test_wms_mock_rack_operation_move_out_then_allocate_releases_position(monkeypatch) -> None:
    mock_post_callback = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(wms_mock_server, "_post_callback", mock_post_callback)
    wms_mock_server.mock_wms_state.work_positions["SINGLE_LAYER_A"] = "RACK-001"
    old_rack = wms_mock_server.mock_wms_state.rack_pool["RACK-001"]
    old_rack["status"] = "ACTIVE"
    old_rack["active_position_code"] = "SINGLE_LAYER_A"
    old_rack["allocated_operation_key"] = "op-old-active"

    with TestClient(wms_mock_server.app) as client:
        response = client.post(
            "/api/wms/rack-operation",
            json=_rack_replace_payload("op-replace-active"),
        )

    assert response.status_code == 200
    callback_payload = mock_post_callback.await_args.args[1]
    assert callback_payload["callback_type"] == "WMS_RACK_ARRIVED"
    assert callback_payload["dispatch_key"] == "rack-operation:op-replace-active:2:ALLOCATE_AND_MOVE_RACK"
    assert callback_payload["request_id"] == "rack-operation:op-replace-active:2:ALLOCATE_AND_MOVE_RACK"
    assert callback_payload["rack_code"] == "RACK-3CELL-001"
    assert wms_mock_server.mock_wms_state.rack_pool["RACK-001"]["status"] == "MOVED_OUT"
    assert wms_mock_server.mock_wms_state.rack_pool["RACK-001"]["active_position_code"] is None
    new_rack = wms_mock_server.mock_wms_state.rack_pool["RACK-3CELL-001"]
    assert new_rack["status"] == "ACTIVE"
    assert new_rack["active_position_code"] == "SINGLE_LAYER_A"
    assert wms_mock_server.mock_wms_state.work_positions["SINGLE_LAYER_A"] == "RACK-3CELL-001"


def test_wms_mock_rack_operation_move_out_mismatch_uses_failure_callback_contract(monkeypatch) -> None:
    mock_post_callback = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(wms_mock_server, "_post_callback", mock_post_callback)
    wms_mock_server.mock_wms_state.work_positions["SINGLE_LAYER_A"] = "RACK-001"
    wms_mock_server.mock_wms_state.rack_pool["RACK-001"]["status"] = "ACTIVE"
    wms_mock_server.mock_wms_state.rack_pool["RACK-6CELL-001"]["status"] = "ACTIVE"
    payload = _rack_replace_payload("op-move-out-mismatch")
    payload["rack_tasks"][0]["rack_code"] = "RACK-6CELL-001"

    with TestClient(wms_mock_server.app) as client:
        response = client.post("/api/wms/rack-operation", json=payload)

    assert response.status_code == 200
    failure_payload = mock_post_callback.await_args.args[1]
    _assert_failure_callback_contract(
        failure_payload,
        dispatch_key="rack-operation:op-move-out-mismatch:1:MOVE_OUT_ACTIVE_RACK",
        operation_key="op-move-out-mismatch",
        reason_code="MOVE_OUT_RACK_MISMATCH",
    )
    assert wms_mock_server.mock_wms_state.work_positions["SINGLE_LAYER_A"] == "RACK-001"
    assert wms_mock_server.mock_wms_state.rack_pool["RACK-6CELL-001"]["status"] == "ACTIVE"


def test_wms_mock_rack_operation_move_out_empty_position_uses_failure_callback_contract(monkeypatch) -> None:
    mock_post_callback = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(wms_mock_server, "_post_callback", mock_post_callback)
    payload = _rack_replace_payload("op-move-out-empty-position")

    with TestClient(wms_mock_server.app) as client:
        response = client.post("/api/wms/rack-operation", json=payload)

    assert response.status_code == 200
    failure_payload = mock_post_callback.await_args.args[1]
    _assert_failure_callback_contract(
        failure_payload,
        dispatch_key="rack-operation:op-move-out-empty-position:1:MOVE_OUT_ACTIVE_RACK",
        operation_key="op-move-out-empty-position",
        reason_code="MOVE_OUT_RACK_MISMATCH",
        reason_message="工位 SINGLE_LAYER_A 当前活动货架为 无，不是 RACK-001",
    )
    assert wms_mock_server.mock_wms_state.rack_pool["RACK-001"]["status"] == "AVAILABLE"


def test_wms_mock_recent_operations_keeps_bounded_history() -> None:
    for index in range(wms_mock_server.RECENT_OPERATION_LIMIT + 5):
        wms_mock_server.mock_wms_state._record_operation({"operation_key": f"op-{index}"})

    recent_operations = wms_mock_server.mock_wms_state.recent_operations
    assert len(recent_operations) == wms_mock_server.RECENT_OPERATION_LIMIT
    assert recent_operations[0]["operation_key"] == "op-5"


def test_wms_mock_debug_racks_returns_pool_positions_and_recent_operations(monkeypatch) -> None:
    mock_post_callback = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(wms_mock_server, "_post_callback", mock_post_callback)

    with TestClient(wms_mock_server.app) as client:
        client.post(
            "/api/wms/rack-operation",
            json=_rack_allocate_payload("op-debug-racks", SEVEN_INCH_MATERIAL),
        )
        response = client.get("/debug/racks")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["work_positions"]["SINGLE_LAYER_A"] == "RACK-001"
    assert data["racks"]["RACK-001"]["status"] == "ACTIVE"
    assert data["recent_operations"][-1]["operation_key"] == "op-debug-racks"
    assert data["recent_operations"][-1]["rack_code"] == "RACK-001"


def test_wms_mock_debug_rack_status_allows_manual_fault_setup() -> None:
    with TestClient(wms_mock_server.app) as client:
        response = client.post(
            "/debug/racks/RACK-3CELL-001/status",
            json={"status": "UNAVAILABLE", "current_location": "MAINTENANCE"},
        )

    assert response.status_code == 200
    rack = response.json()["data"]
    assert rack["rack_id"] == "RACK-3CELL-001"
    assert rack["status"] == "UNAVAILABLE"
    assert rack["current_location"] == "MAINTENANCE"
    assert wms_mock_server.mock_wms_state.rack_pool["RACK-3CELL-001"]["status"] == "UNAVAILABLE"


def test_wms_mock_rack_query_returns_copy_not_internal_state() -> None:
    with TestClient(wms_mock_server.app) as client:
        response = client.get("/api/wms/racks/RACK-001")

    assert response.status_code == 200
    rack_payload = response.json()["data"]
    rack_payload["status"] = "MUTATED_BY_TEST"
    assert wms_mock_server.mock_wms_state.rack_pool["RACK-001"]["status"] == "AVAILABLE"


def test_wms_mock_rack_list_returns_copy_not_internal_state() -> None:
    with TestClient(wms_mock_server.app) as client:
        response = client.get("/api/wms/racks")

    assert response.status_code == 200
    rack_payload = response.json()["data"][0]
    rack_payload["status"] = "MUTATED_BY_TEST"
    assert wms_mock_server.mock_wms_state.rack_pool[rack_payload["rack_id"]]["status"] == "AVAILABLE"


@pytest.mark.asyncio
async def test_wms_mock_state_concurrent_allocate_and_move_rack_requests_do_not_share_rack() -> None:
    async def allocate(operation_key: str, target_position_code: str) -> str:
        callback_payload = await wms_mock_server.mock_wms_state.allocate_rack_for_payload(
            _rack_allocate_payload(operation_key, SEVEN_INCH_MATERIAL, target_position_code=target_position_code)
        )
        return str(callback_payload["rack_code"])

    first_rack_code, second_rack_code = await asyncio.gather(
        allocate("op-concurrent-1", "SINGLE_LAYER_A"),
        allocate("op-concurrent-2", "SINGLE_LAYER_B"),
    )

    assert first_rack_code != second_rack_code
    assert {first_rack_code, second_rack_code} == {"RACK-001", "RACK-6CELL-001"}


@pytest.mark.asyncio
async def test_wms_mock_rack_operation_route_concurrent_requests_do_not_share_rack(monkeypatch) -> None:
    mock_post_callback = AsyncMock(return_value={"delivered": True})
    monkeypatch.setattr(wms_mock_server, "_post_callback", mock_post_callback)
    transport = httpx.ASGITransport(app=wms_mock_server.app)

    async def post_operation(operation_key: str, target_position_code: str) -> None:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/wms/rack-operation",
                json=_rack_allocate_payload(
                    operation_key,
                    SEVEN_INCH_MATERIAL,
                    target_position_code=target_position_code,
                ),
            )
        assert response.status_code == 200
        assert response.json()["data"]["accepted"] is True

    await asyncio.gather(
        post_operation("op-route-concurrent-1", "SINGLE_LAYER_A"),
        post_operation("op-route-concurrent-2", "SINGLE_LAYER_B"),
    )

    delivered_rack_codes = {call.args[1]["rack_code"] for call in mock_post_callback.await_args_list}
    assert delivered_rack_codes == {"RACK-001", "RACK-6CELL-001"}
    assert (
        wms_mock_server.mock_wms_state.work_positions["SINGLE_LAYER_A"]
        != (wms_mock_server.mock_wms_state.work_positions["SINGLE_LAYER_B"])
    )


def test_wms_mock_rack_operation_supplies_three_cell_bins_for_13inch_material() -> None:
    callback_payload = wms_mock_server._rack_operation_callback_payload(
        {
            "request_id": "rack-operation:op-13inch:1:ALLOCATE_AND_MOVE_RACK",
            "dispatch_key": "rack-operation:op-13inch:1:ALLOCATE_AND_MOVE_RACK",
            "callback_type": "WMS_RACK_ARRIVED",
            "operation_key": "op-13inch",
            "operation_type": "REPLACE_CLASSIFIER_WORK_RACK",
            "sequence_no": 1,
            "task_type": "ALLOCATE_AND_MOVE_RACK",
            "workline_code": "WL-ROUGH-SORTER-TEST",
            "rack_kind": "SINGLE_LAYER",
            "target_position_code": "SINGLE_LAYER_A",
            "material": {
                "HHPN": "IC001",
                "LotCode": "LOT-I",
                "DateCode": "20260413",
                "PkgID": "PKG-IC001-LOT-I-001",
            },
        }
    )

    cells = callback_payload["active_bin_rack"]["cells"]
    assert callback_payload["rack_code"] == "RACK-3CELL-001"
    assert callback_payload["active_bin_rack"]["rack_code"] == "RACK-3CELL-001"
    assert len(cells) == 12
    assert {cell["bin_type"] for cell in cells} == {"3格箱"}
    assert {cell["bin_cell_index"] for cell in cells if cell["rack_slot_code"] == "A"} == {"1", "2", "7"}
    assert {cell["capacity_depth_mm"] for cell in cells if cell["bin_cell_index"] == "7"} == {80.0}


def test_wms_mock_rack_operation_uses_distinct_bin_codes_per_rack() -> None:
    six_cell_payload = wms_mock_server._rack_operation_callback_payload(
        {
            "request_id": "rack-operation:op-7inch:1:ALLOCATE_AND_MOVE_RACK",
            "dispatch_key": "rack-operation:op-7inch:1:ALLOCATE_AND_MOVE_RACK",
            "callback_type": "WMS_RACK_ARRIVED",
            "operation_key": "op-7inch",
            "operation_type": "REPLACE_CLASSIFIER_WORK_RACK",
            "sequence_no": 1,
            "task_type": "ALLOCATE_AND_MOVE_RACK",
            "workline_code": "WL-ROUGH-SORTER-TEST",
            "rack_kind": "SINGLE_LAYER",
            "target_position_code": "SINGLE_LAYER_A",
            "material": {
                "HHPN": "RES001",
                "LotCode": "LOT-R",
                "DateCode": "20260407",
                "PkgID": "PKG-RES001-LOT-R-001",
            },
        }
    )
    three_cell_payload = wms_mock_server._rack_operation_callback_payload(
        {
            "request_id": "rack-operation:op-13inch:1:ALLOCATE_AND_MOVE_RACK",
            "dispatch_key": "rack-operation:op-13inch:1:ALLOCATE_AND_MOVE_RACK",
            "callback_type": "WMS_RACK_ARRIVED",
            "operation_key": "op-13inch",
            "operation_type": "REPLACE_CLASSIFIER_WORK_RACK",
            "sequence_no": 1,
            "task_type": "ALLOCATE_AND_MOVE_RACK",
            "workline_code": "WL-ROUGH-SORTER-TEST",
            "rack_kind": "SINGLE_LAYER",
            "target_position_code": "SINGLE_LAYER_A",
            "material": {
                "HHPN": "IC001",
                "LotCode": "LOT-I",
                "DateCode": "20260413",
                "PkgID": "PKG-IC001-LOT-I-001",
            },
        }
    )

    six_cell_bins = {mount["bin_code"] for mount in six_cell_payload["bin_mounts"]}
    three_cell_bins = {mount["bin_code"] for mount in three_cell_payload["bin_mounts"]}
    assert six_cell_payload["rack_code"] == "RACK-001"
    assert three_cell_payload["rack_code"] == "RACK-3CELL-001"
    assert six_cell_bins.isdisjoint(three_cell_bins)


def test_wms_mock_rack_operation_keeps_rack_bin_cell_physical_constraints() -> None:
    six_cell_payload = wms_mock_server._rack_operation_callback_payload(
        {
            "request_id": "rack-operation:op-physical-7inch:1:ALLOCATE_AND_MOVE_RACK",
            "dispatch_key": "rack-operation:op-physical-7inch:1:ALLOCATE_AND_MOVE_RACK",
            "callback_type": "WMS_RACK_ARRIVED",
            "operation_key": "op-physical-7inch",
            "task_type": "ALLOCATE_AND_MOVE_RACK",
            "rack_code": "RACK-3CELL-001",
            "material": {
                "HHPN": "CAP001",
                "LotCode": "LOT-A",
                "DateCode": "20260409",
                "PkgID": "PKG-CAP001-LOT-A-001",
            },
        }
    )
    three_cell_payload = wms_mock_server._rack_operation_callback_payload(
        {
            "request_id": "rack-operation:op-physical-13inch:1:ALLOCATE_AND_MOVE_RACK",
            "dispatch_key": "rack-operation:op-physical-13inch:1:ALLOCATE_AND_MOVE_RACK",
            "callback_type": "WMS_RACK_ARRIVED",
            "operation_key": "op-physical-13inch",
            "task_type": "ALLOCATE_AND_MOVE_RACK",
            "rack_code": "RACK-001",
            "material": {
                "HHPN": "IC001",
                "LotCode": "LOT-I",
                "DateCode": "20260413",
                "PkgID": "PKG-IC001-LOT-I-001",
            },
        }
    )

    assert six_cell_payload["rack_code"] == "RACK-001"
    assert {cell["bin_type"] for cell in six_cell_payload["active_bin_rack"]["cells"]} == {"6格箱", "3格箱"}
    assert len(six_cell_payload["bin_mounts"]) == 4
    assert len(six_cell_payload["active_bin_rack"]["cells"]) == 18
    for mount in six_cell_payload["bin_mounts"]:
        slot_cells = [
            cell
            for cell in six_cell_payload["active_bin_rack"]["cells"]
            if cell["rack_slot_code"] == mount["rack_slot_code"]
        ]
        assert {cell["bin_code"] for cell in slot_cells} == {mount["bin_code"]}
        if mount["rack_slot_code"] in {"A", "B"}:
            assert {cell["bin_cell_index"] for cell in slot_cells} == {"1", "2", "3", "4", "5", "6"}
        else:
            assert {cell["bin_cell_index"] for cell in slot_cells} == {"1", "2", "7"}

    assert three_cell_payload["rack_code"] == "RACK-001"
    assert {cell["bin_type"] for cell in three_cell_payload["active_bin_rack"]["cells"]} == {"6格箱", "3格箱"}
    assert len(three_cell_payload["bin_mounts"]) == 4
    assert len(three_cell_payload["active_bin_rack"]["cells"]) == 18
    for mount in three_cell_payload["bin_mounts"]:
        slot_cells = [
            cell
            for cell in three_cell_payload["active_bin_rack"]["cells"]
            if cell["rack_slot_code"] == mount["rack_slot_code"]
        ]
        assert {cell["bin_code"] for cell in slot_cells} == {mount["bin_code"]}
        if mount["rack_slot_code"] in {"A", "B"}:
            assert {cell["bin_cell_index"] for cell in slot_cells} == {"1", "2", "3", "4", "5", "6"}
        else:
            assert {cell["bin_cell_index"] for cell in slot_cells} == {"1", "2", "7"}
            assert {cell["capacity_depth_mm"] for cell in slot_cells if cell["bin_cell_index"] == "7"} == {80.0}


def test_wms_mock_large_reel_detection_does_not_match_dimension_substrings() -> None:
    assert wms_mock_server._has_large_reel_size({"reel_diameter": "13inch"}) is True
    assert wms_mock_server._has_large_reel_size({"reel_diameter": "330.0"}) is True
    assert wms_mock_server._has_large_reel_size({"reel_diameter": "113mm"}) is False
    assert wms_mock_server._has_large_reel_size({"reel_diameter": "150mm"}) is False


@pytest.mark.parametrize("raw", ["nan", "inf", "-inf", "0", "-1", "invalid"])
def test_wms_mock_cell_capacity_env_rejects_invalid_values(monkeypatch, raw: str) -> None:
    monkeypatch.setenv("MOCK_WMS_CELL_CAPACITY_DEPTH_MM", raw)

    assert wms_mock_server._positive_float_env("MOCK_WMS_CELL_CAPACITY_DEPTH_MM") is None


def test_wms_mock_cell_capacity_env_accepts_positive_finite_value(monkeypatch) -> None:
    monkeypatch.setenv("MOCK_WMS_CELL_CAPACITY_DEPTH_MM", "30.5")

    assert wms_mock_server._positive_float_env("MOCK_WMS_CELL_CAPACITY_DEPTH_MM") == 30.5


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


@pytest.mark.asyncio
async def test_wms_mock_move_rack_updates_location_status_and_releases_position() -> None:
    wms_mock_server.mock_wms_state.work_positions["SINGLE_LAYER_A"] = "RACK-001"
    rack = wms_mock_server.mock_wms_state.rack_pool["RACK-001"]
    rack["status"] = "ACTIVE"
    rack["active_position_code"] = "SINGLE_LAYER_A"
    rack["allocated_operation_key"] = "op-active"

    callback_payload = await wms_mock_server.mock_wms_state.apply_operation(
        {
            "request_id": "rack-operation:op-002:2:MOVE_RACK",
            "dispatch_key": "rack-operation:op-002:2:MOVE_RACK",
            "callback_type": "WMS_RACK_TASK_RESULT",
            "operation_key": "op-002",
            "sequence_no": 2,
            "task_type": "MOVE_RACK",
            "rack_code": "RACK-001",
            "target_position_code": "EMPTY_RACK_AREA",
        }
    )

    assert callback_payload["callback_type"] == "WMS_RACK_TASK_RESULT"
    assert wms_mock_server.mock_wms_state.rack_pool["RACK-001"]["current_location"] == "EMPTY_RACK_AREA"
    assert wms_mock_server.mock_wms_state.rack_pool["RACK-001"]["status"] == "MOVED_OUT"
    assert wms_mock_server.mock_wms_state.rack_pool["RACK-001"]["active_position_code"] is None
    assert wms_mock_server.mock_wms_state.work_positions["SINGLE_LAYER_A"] is None


@pytest.mark.asyncio
async def test_wms_mock_move_rack_callback_preserves_requested_three_cell_rack() -> None:
    wms_mock_server.mock_wms_state.rack_pool["RACK-3CELL-001"]["status"] = "ACTIVE"

    callback_payload = await wms_mock_server.mock_wms_state.apply_operation(
        {
            "request_id": "rack-operation:op-3cell-move:2:MOVE_RACK",
            "dispatch_key": "rack-operation:op-3cell-move:2:MOVE_RACK",
            "callback_type": "WMS_RACK_TASK_RESULT",
            "operation_key": "op-3cell-move",
            "sequence_no": 2,
            "task_type": "MOVE_RACK",
            "rack_code": "RACK-3CELL-001",
            "target_position_code": "EMPTY_RACK_AREA",
        }
    )

    assert callback_payload["callback_type"] == "WMS_RACK_TASK_RESULT"
    assert callback_payload["rack_code"] == "RACK-3CELL-001"
    assert callback_payload["active_bin_rack"]["rack_code"] == "RACK-3CELL-001"
    assert wms_mock_server.mock_wms_state.rack_pool["RACK-3CELL-001"]["current_location"] == "EMPTY_RACK_AREA"
