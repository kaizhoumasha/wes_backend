"""Sorter inbound 本机 MOCK 合同。

这些测试只验证本机 mock 能表达目标态入库语义，不代表 evidence profile 闭合。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.app.wms_integration.ports.confirm_inbound_operation import OPERATION_IDENTITY as CONFIRM_INBOUND_IDENTITY
from src.app.wms_integration.ports.notify_pkg_binding_operation import (
    OPERATION_IDENTITY as NOTIFY_PACKAGE_BINDING_IDENTITY,
)
from tests.mock import wms_mock_server


def setup_function() -> None:
    wms_mock_server.reset_mock_wms_state()


def test_rough_sorter_mock_separates_local_physical_fact_from_wms_sync_failure() -> None:
    """粗分机正常流先保留本地物理事实，WMS 失败只推进同步 hold/reconciliation。"""

    with TestClient(wms_mock_server.app) as client:
        response = client.post(
            "/api/wms/fulfillment/rough-sorter-inbound-preview",
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


def test_sorter_inbound_mock_enforces_join_gate_and_prefetch_policy() -> None:
    """南向投料前必须同时满足料箱、格位预约和等待条件；未声明预取时默认不预取。"""

    with TestClient(wms_mock_server.app) as client:
        allowed_response = client.post(
            "/api/wms/fulfillment/sorter-inbound-preview",
            json={
                "request_id": "mock-sorter-inbound-001",
                "expected_authorized_bin_ids": ["BIN-A-001"],
                "actual_scanned_bin_id": "BIN-A-001",
                "target_bin_position_state": "AT_WORK_POSITION",
                "target_cell_reservable": True,
                "cell_reservation_state": "RESERVED",
                "waiting_deadline_declared": True,
                "scanner_platform_state": "BUSY",
                "manifest": {},
            },
        )
        rejected_response = client.post(
            "/api/wms/fulfillment/sorter-inbound-preview",
            json={
                "request_id": "mock-sorter-inbound-unauthorized-001",
                "expected_authorized_bin_ids": ["BIN-A-001"],
                "actual_scanned_bin_id": "BIN-X-999",
                "target_bin_position_state": "IN_TRANSFER",
                "target_cell_reservable": False,
                "cell_reservation_state": "NONE",
                "waiting_deadline_declared": False,
                "scanner_platform_state": "BUSY",
                "manifest": {},
            },
        )

    assert allowed_response.status_code == 200
    allowed = allowed_response.json()["data"]
    assert allowed["prefetch_policy"] == {
        "source_arm_prefetch_capacity": 0,
        "can_pick_next_material": False,
        "requires_scanner_platform_free": True,
    }
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
    assert set(rejected["join_gate"]["missing_conditions"]) == {
        "AUTHORIZED_BIN_RESOLVED",
        "TARGET_BIN_AT_WORK_POSITION",
        "TARGET_CELL_RESERVABLE",
        "CELL_RESERVATION_RESERVED",
        "WAITING_DEADLINE_DECLARED",
    }


def test_sorter_inbound_mock_validates_positive_prefetch_manifest() -> None:
    """显式开启扫码平台预取时，manifest 必须声明 ECS 能力、缓存容量和超时策略。"""

    base_payload = {
        "request_id": "mock-sorter-prefetch-001",
        "expected_authorized_bin_ids": ["BIN-A-001"],
        "actual_scanned_bin_id": "BIN-A-001",
        "target_bin_position_state": "AT_WORK_POSITION",
        "target_cell_reservable": True,
        "cell_reservation_state": "RESERVED",
        "waiting_deadline_declared": True,
        "scanner_platform_state": "BUSY",
    }
    with TestClient(wms_mock_server.app) as client:
        invalid_response = client.post(
            "/api/wms/fulfillment/sorter-inbound-preview",
            json={**base_payload, "manifest": {"source_arm_prefetch_capacity": 2}},
        )
        valid_response = client.post(
            "/api/wms/fulfillment/sorter-inbound-preview",
            json={
                **base_payload,
                "manifest": {
                    "source_arm_prefetch_capacity": 2,
                    "ecs_capabilities": ["SOURCE_ARM_PREFETCH"],
                    "prefetch_buffer_capacity": 2,
                    "prefetch_timeout_ms": 5000,
                },
            },
        )

    assert invalid_response.status_code == 200
    invalid = invalid_response.json()["data"]
    assert invalid["manifest_validation"]["allowed"] is False
    assert set(invalid["manifest_validation"]["errors"]) == {
        "ECS_SOURCE_ARM_PREFETCH_CAPABILITY_REQUIRED",
        "PREFETCH_BUFFER_CAPACITY_TOO_SMALL",
        "PREFETCH_TIMEOUT_REQUIRED",
    }
    assert invalid["prefetch_policy"]["can_pick_next_material"] is False

    assert valid_response.status_code == 200
    valid = valid_response.json()["data"]
    assert valid["manifest_validation"] == {"allowed": True, "errors": []}
    assert valid["prefetch_policy"] == {
        "source_arm_prefetch_capacity": 2,
        "can_pick_next_material": True,
        "requires_scanner_platform_free": False,
    }


def test_ctu_batch_mock_parent_view_requires_child_convergence() -> None:
    """CTU 父请求查询视图必须暴露子项缺失、重复和部分失败，不能只显示父成功。"""

    with TestClient(wms_mock_server.app) as client:
        response = client.post(
            "/api/wms/fulfillment/ctu-batch-preview",
            json={
                "parent_request_id": "mock-ctu-parent-001",
                "parent_callback_state": "SUCCESS",
                "child_items": [
                    {
                        "sequence_no": 1,
                        "placeholder_key": "PH-BIN-001",
                        "resolved_bin_id": "BIN-A-001",
                        "stage_status": "COMPLETED",
                        "source_event_id": "ctu-child-001",
                    },
                    {
                        "sequence_no": 2,
                        "placeholder_key": "PH-BIN-002",
                        "resolved_bin_id": None,
                        "stage_status": "COMPLETED",
                        "source_event_id": "ctu-child-002",
                    },
                    {
                        "sequence_no": 2,
                        "placeholder_key": "PH-BIN-003",
                        "resolved_bin_id": "BIN-A-003",
                        "stage_status": "FAILED",
                        "source_event_id": "ctu-child-003",
                    },
                ],
            },
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["environment"] == "LOCAL_MOCK_ONLY"
    assert data["production_write_path"] is False
    assert data["parent_callback_state"] == "SUCCESS"
    assert data["parent_business_completed"] is False
    assert data["parent_projection_state"] == "RECONCILING"
    assert data["query_view"]["child_count"] == 3
    assert data["query_view"]["missing_resolved_placeholders"] == ["PH-BIN-002"]
    assert data["query_view"]["duplicate_sequence_nos"] == [2]
    assert data["query_view"]["failed_child_placeholders"] == ["PH-BIN-003"]
    assert data["query_view"]["operator_summary_state"] == "RECONCILING"
