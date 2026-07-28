"""Material-flow sorter inbound preview capability 合同。

preview 只暴露 `wms.fulfillment.notify_pkg_binding@v1` 和
`wms.inventory.confirm_inbound@v1` 稳定 identity。
"""

from __future__ import annotations

from src.app.runtime.capabilities.material_flow.sorter_inbound_preview_service import (
    SorterInboundPreviewService,
)
from src.app.wms_integration.ports.fulfillment_operations import NOTIFY_PKG_BINDING
from src.app.wms_integration.ports.inventory_operations import CONFIRM_INBOUND

CONFIRM_INBOUND_IDENTITY = CONFIRM_INBOUND.identity
NOTIFY_PACKAGE_BINDING_IDENTITY = NOTIFY_PKG_BINDING.identity


def test_rough_sorter_preview_keeps_local_fact_and_splits_wms_effect_ports() -> None:
    """粗分机 preview 必须拆分本地物理事实与 WMS 同步失败。"""

    service = SorterInboundPreviewService()

    preview = service.preview_rough_sorter_inbound(
        {
            "request_id": "preview-rough-001",
            "object_key": "PKG-ROUGH-001",
            "target_cell_code": "CELL-A-01",
            "local_physical_completed": True,
            "wms_pkg_binding_result": "REJECTED",
        }
    )

    assert preview["environment"] == "LOCAL_MOCK_ONLY"
    assert preview["production_write_path"] is False
    assert preview["legacy_plugin_entry_used"] is False
    assert preview["local_position_state"] == "LOCAL_PHYSICAL_COMPLETED"
    assert preview["wms_sync_state"] == "WMS_SYNC_PENDING"
    assert preview["business_completion_state"] == "RECONCILING"
    assert preview["preserve_local_physical_fact"] is True
    assert preview["effect_ports"] == {
        "pkg_binding": NOTIFY_PACKAGE_BINDING_IDENTITY,
        "inventory_transaction": CONFIRM_INBOUND_IDENTITY,
    }


def test_sorter_preview_enforces_join_gate_and_ack_causality_without_platform_inference() -> None:
    """WES 只表达 PICK ACK 因果链，不推断扫码平台空闲或预取容量。"""

    service = SorterInboundPreviewService()

    blocked = service.preview_sorter_inbound(
        {
            "request_id": "preview-sorter-blocked-001",
            "expected_authorized_bin_ids": ["BIN-A-001"],
            "actual_scanned_bin_id": "BIN-X-999",
            "target_bin_position_state": "IN_TRANSFER",
            "target_cell_reservable": False,
            "cell_reservation_state": "NONE",
            "waiting_deadline_declared": False,
            "southbound_pick_acknowledged": False,
        }
    )
    allowed = service.preview_sorter_inbound(
        {
            "request_id": "preview-sorter-allowed-001",
            "expected_authorized_bin_ids": ["BIN-A-001"],
            "actual_scanned_bin_id": "BIN-A-001",
            "target_bin_position_state": "AT_WORK_POSITION",
            "target_cell_reservable": True,
            "cell_reservation_state": "RESERVED",
            "waiting_deadline_declared": True,
            "southbound_pick_acknowledged": True,
        }
    )

    assert blocked["legacy_plugin_entry_used"] is False
    assert blocked["join_gate"]["allowed"] is False
    assert set(blocked["join_gate"]["missing_conditions"]) == {
        "AUTHORIZED_BIN_RESOLVED",
        "TARGET_BIN_AT_WORK_POSITION",
        "TARGET_CELL_RESERVABLE",
        "CELL_RESERVATION_RESERVED",
        "WAITING_DEADLINE_DECLARED",
    }
    assert blocked["next_northbound_pick_triggered"] is False
    assert blocked["runtime_hold_required"] is True

    assert allowed["join_gate"]["allowed"] is True
    assert allowed["next_northbound_pick_triggered"] is True
    assert "prefetch_policy" not in allowed
    assert "manifest_validation" not in allowed


def test_full_box_preview_pre_diverts_before_sorter_station_admission() -> None:
    """满箱物料必须在分拣机逐件流程前分流。"""

    service = SorterInboundPreviewService()

    preview = service.preview_full_box_exchange(
        {
            "request_id": "preview-full-box-001",
            "rack_code": "RACK-6CELL-001",
            "rack_side": "A",
            "exchange_zone": "FULL_BOX_EXCHANGE_ZONE_A",
            "full_box_object_keys": ["PKG-FULL-001", "PKG-FULL-002"],
            "remaining_object_keys": ["PKG-FULL-001", "PKG-PIECE-001"],
        }
    )

    assert preview["production_write_path"] is False
    assert preview["legacy_plugin_entry_used"] is False
    assert preview["fulfillment_action"] == "FULL_BOX_EXCHANGE"
    assert preview["batch_key"] == "RACK-6CELL-001:A"
    assert preview["station_admission_blocked_until_exchange_completed"] is True
    assert preview["box_level_inventory_transaction_required"] is True
    assert preview["sorting_candidate_object_keys"] == ["PKG-PIECE-001"]


def test_change_rack_face_preview_is_independent_fulfillment() -> None:
    """CHANGE_RACK_FACE 不能被 full-box exchange 成功语义吞并。"""

    service = SorterInboundPreviewService()

    preview = service.preview_change_rack_face(
        {
            "request_id": "preview-change-face-001",
            "parent_request_id": "preview-full-box-001",
            "rack_code": "RACK-6CELL-001",
            "from_rack_side": "A",
            "to_rack_side": "B",
        }
    )

    assert preview["production_write_path"] is False
    assert preview["legacy_plugin_entry_used"] is False
    assert preview["fulfillment_action"] == "CHANGE_RACK_FACE"
    assert preview["independent_fulfillment"] is True
    assert preview["does_not_mark_full_box_exchange_completed"] is True


def test_ctu_batch_preview_parent_success_does_not_hide_child_issues() -> None:
    """CTU 父成功不能掩盖子项缺失、重复和部分失败。"""

    service = SorterInboundPreviewService()

    preview = service.preview_ctu_batch(
        {
            "parent_request_id": "preview-ctu-parent-001",
            "parent_callback_state": "SUCCESS",
            "child_items": [
                {
                    "sequence_no": 1,
                    "placeholder_key": "PH-BIN-001",
                    "resolved_bin_id": "BIN-A-001",
                    "stage_status": "COMPLETED",
                },
                {
                    "sequence_no": 2,
                    "placeholder_key": "PH-BIN-002",
                    "resolved_bin_id": None,
                    "stage_status": "COMPLETED",
                },
                {
                    "sequence_no": 2,
                    "placeholder_key": "PH-BIN-003",
                    "resolved_bin_id": "BIN-A-003",
                    "stage_status": "FAILED",
                },
            ],
        }
    )

    assert preview["production_write_path"] is False
    assert preview["legacy_plugin_entry_used"] is False
    assert preview["parent_business_completed"] is False
    assert preview["parent_projection_state"] == "RECONCILING"
    assert preview["query_view"]["missing_resolved_placeholders"] == ["PH-BIN-002"]
    assert preview["query_view"]["duplicate_sequence_nos"] == [2]
    assert preview["query_view"]["failed_child_placeholders"] == ["PH-BIN-003"]
