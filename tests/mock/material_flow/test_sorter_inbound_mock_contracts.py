"""Sorter inbound 本机 MOCK 合同。

这些测试只验证本机 mock 能表达目标态入库语义，不代表 evidence profile 闭合。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.app.wms_integration.ports.fulfillment_operations import NOTIFY_PKG_BINDING
from src.app.wms_integration.ports.inventory_operations import CONFIRM_INBOUND
from tests.mock import wms_mock_server

CONFIRM_INBOUND_IDENTITY = CONFIRM_INBOUND.identity
NOTIFY_PACKAGE_BINDING_IDENTITY = NOTIFY_PKG_BINDING.identity


def setup_function() -> None:
    wms_mock_server.reset_mock_wms_state()


def test_rough_sorter_mock_separates_local_physical_fact_from_wms_sync_failure() -> None:
    """粗分机正常流先保留本地物理事实，WMS 失败只推进同步 hold/reconciliation。"""

    with TestClient(wms_mock_server.app) as client:
        response = client.post(
            "/debug/wms/fulfillment/rough-sorter-inbound-preview",
            json={
                "request_id": "mock-rough-sorter-001",
                "object_key": "PKG-ROUGH-001",
                "grn_id": "GRN-001",
                "target_cell_code": "CELL-A-01",
                "local_physical_completed": True,
                "wms_pkg_binding_result": "REJECTED",
            },
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["environment"] == "LOCAL_MOCK_ONLY"
    assert data["production_write_path"] is False
    assert data["ordered_steps"] == [
        "SCAN_AND_MEASURE",
        "WMS_GRN_BINDING_CHECK",
        "SOURCE_ARM_TO_CONVEYOR",
        "ROUGH_SORTER_TO_OUTBOUND",
        "CELL_RESERVATION",
        "OUTBOUND_ARM_TO_CELL",
        "LOCAL_PHYSICAL_FACT",
        "WMS_SYNC",
    ]
    assert data["local_position_state"] == "LOCAL_PHYSICAL_COMPLETED"
    assert data["wms_sync_state"] == "WMS_SYNC_PENDING"
    assert data["business_completion_state"] == "RECONCILING"
    assert data["preserve_local_physical_fact"] is True
    assert data["next_object_admission_allowed"] is True
    assert data["effect_ports"] == {
        "pkg_binding": NOTIFY_PACKAGE_BINDING_IDENTITY,
        "inventory_transaction": CONFIRM_INBOUND_IDENTITY,
    }


def test_sorter_inbound_mock_enforces_join_gate_and_pick_ack_causality() -> None:
    """南向投料 join gate 与下一北向取料只通过 PICK ACK 建立因果。"""

    with TestClient(wms_mock_server.app) as client:
        allowed_response = client.post(
            "/debug/wms/fulfillment/sorter-inbound-preview",
            json={
                "request_id": "mock-sorter-inbound-001",
                "expected_authorized_bin_ids": ["BIN-A-001"],
                "actual_scanned_bin_id": "BIN-A-001",
                "target_bin_position_state": "AT_WORK_POSITION",
                "target_cell_reservable": True,
                "cell_reservation_state": "RESERVED",
                "waiting_deadline_declared": True,
                "southbound_pick_acknowledged": True,
            },
        )
        rejected_response = client.post(
            "/debug/wms/fulfillment/sorter-inbound-preview",
            json={
                "request_id": "mock-sorter-inbound-unauthorized-001",
                "expected_authorized_bin_ids": ["BIN-A-001"],
                "actual_scanned_bin_id": "BIN-X-999",
                "target_bin_position_state": "IN_TRANSFER",
                "target_cell_reservable": False,
                "cell_reservation_state": "NONE",
                "waiting_deadline_declared": False,
                "southbound_pick_acknowledged": False,
            },
        )

    assert allowed_response.status_code == 200
    allowed = allowed_response.json()["data"]
    assert allowed["next_northbound_pick_triggered"] is True
    assert "prefetch_policy" not in allowed
    assert "manifest_validation" not in allowed
    assert allowed["ordered_steps"] == [
        "STATION_ADMISSION",
        "WMS_CTU_BIN_INFEED",
        "SCAN1_AUTHORIZED_RESOLVE",
        "SCAN2_ROUTE_DECISION",
        "SCAN3_RETURN_OR_NG_ROUTE",
        "SOURCE_ARM_TO_SCANNER_PLATFORM",
        "CELL_RESERVATION",
        "SOUTH_ARM_DROP",
        "LOCAL_PHYSICAL_FACT",
        "WMS_SYNC",
    ]
    assert allowed["join_gate"]["allowed"] is True
    assert allowed["join_gate"]["missing_conditions"] == []
    assert allowed["local_position_state"] == "LOCAL_PHYSICAL_COMPLETED"
    assert allowed["wms_sync_state"] == "READY_TO_SYNC"

    assert rejected_response.status_code == 200
    rejected = rejected_response.json()["data"]
    assert rejected["join_gate"]["allowed"] is False
    assert rejected["business_completion_state"] == "RECONCILING"
    assert rejected["ng_route_state"] == "NG_OR_RUNTIME_HOLD"
    assert rejected["runtime_hold_required"] is True
    assert rejected["next_northbound_pick_triggered"] is False
    assert set(rejected["join_gate"]["missing_conditions"]) == {
        "AUTHORIZED_BIN_RESOLVED",
        "TARGET_BIN_AT_WORK_POSITION",
        "TARGET_CELL_RESERVABLE",
        "CELL_RESERVATION_RESERVED",
        "WAITING_DEADLINE_DECLARED",
    }
