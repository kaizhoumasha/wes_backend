"""29 项 WMS operation 的最小 typed fixture。

本文件是 Mock WMS 的独立测试资产；覆盖测试会按静态 registry fail closed 校验键集合与模型。
"""

from src.app.wms_integration.operation_registry import WMS_OPERATIONS
from tests.mock.wms_fixture_matrix import build_operation_fixture_matrix

REQUEST_FIXTURES = {
    "wms.master_data.get_material@v1": {"material_code": "MAT-001"},
    "wms.master_data.list_materials@v1": {"page_size": 100},
    "wms.master_data.list_zones@v1": {"page_size": 100},
    "wms.master_data.list_locations@v1": {"page_size": 100, "zone_code": "ZONE-A"},
    "wms.master_data.get_rack@v1": {"rack_id": "RACK-001"},
    "wms.master_data.list_racks@v1": {"page_size": 100, "rack_type": "FIVE_LAYER"},
    "wms.master_data.get_bin@v1": {"bin_id": "BIN-001"},
    "wms.document.get_grn@v1": {"grn_id": "GRN-001"},
    "wms.document.list_grn_packages@v1": {"grn_id": "GRN-001", "page_size": 100},
    "wms.document.get_pick_order@v1": {"pick_order_id": "PICK-001"},
    "wms.document.get_outbound_order@v1": {"outbound_order_id": "OUT-001"},
    "wms.document.get_wave@v1": {"wave_id": "WMS-WV-001"},
    "wms.document.get_task_snapshot@v1": {"task_id": "TASK-001"},
    "wms.inventory.query_inventory@v1": {"material_code": "MAT-001", "page_size": 100},
    "wms.inventory.get_reservation@v1": {"reservation_id": "RES-001"},
    "wms.reconciliation.check_bin_drift@v1": {"warehouse_code": "WH-A", "page_size": 100},
    "wms.reconciliation.check_rack_drift@v1": {"warehouse_code": "WH-A", "page_size": 100},
    "wms.reconciliation.check_full_drift@v1": {"warehouse_code": "WH-A", "page_size": 100},
    "wms.inventory.reserve_inventory@v1": {
        "dispatch_key": "dispatch-reserve-001",
        "material_code": "MAT-001",
        "quantity": "10",
        "warehouse_code": "WH-A",
    },
    "wms.inventory.release_reservation@v1": {
        "dispatch_key": "dispatch-release-001",
        "reservation_id": "RES-001",
        "release_reason": "FLOW_COMPLETED",
    },
    "wms.inventory.confirm_inbound@v1": {
        "dispatch_key": "dispatch-inbound-001",
        "inbound_key": "IN-001",
        "material_code": "MAT-001",
        "quantity": "10",
        "pkg_id": "PKG-001",
        "location_code": "LOC-A",
    },
    "wms.inventory.confirm_outbound@v1": {
        "dispatch_key": "dispatch-outbound-001",
        "outbound_key": "OUT-001",
        "material_code": "MAT-001",
        "quantity": "10",
        "reservation_id": "RES-001",
    },
    "wms.inventory.transfer_inventory@v1": {
        "dispatch_key": "dispatch-transfer-001",
        "transfer_key": "TRANSFER-001",
        "material_code": "MAT-001",
        "quantity": "10",
        "source_location_code": "LOC-A",
        "destination_location_code": "LOC-B",
    },
    "wms.inventory.confirm_return_putaway@v1": {
        "dispatch_key": "dispatch-return-001",
        "return_key": "RETURN-001",
        "original_pkg_id": "PKG-001",
        "material_code": "MAT-001",
        "quantity": "9",
        "destination_location_code": "LOC-R",
    },
    "wms.fulfillment.notify_pkg_binding@v1": {
        "dispatch_key": "dispatch-binding-001",
        "pkg_id": "PKG-001",
        "bin_id": "BIN-001",
        "slot_id": "SLOT-001",
        "rack_id": "RACK-001",
        "station_code": "ROUGH-OUT",
    },
    "wms.fulfillment.request_rack_supply@v1": {
        "dispatch_key": "dispatch-supply-001",
        "station_code": "STATION-A",
        "rack_type": "FIVE_LAYER",
        "demand_generation": 1,
    },
    "wms.fulfillment.change_rack_face@v1": {
        "dispatch_key": "dispatch-face-001",
        "rack_id": "RACK-001",
        "station_code": "STATION-A",
        "requested_face": "B",
    },
    "wms.fulfillment.publish_manual_task@v1": {
        "dispatch_key": "dispatch-manual-001",
        "manual_task_key": "MANUAL-001",
        "task_type": "MOVE_OBJECT",
        "object_keys": ["BIN-001"],
        "station_code": "STATION-A",
        "instructions": "Move the bin to the recovery area.",
    },
    "wms.fulfillment.cancel_request@v1": {
        "dispatch_key": "dispatch-cancel-001",
        "target_operation_identity": "wms.fulfillment.request_rack_supply@v1",
        "target_idempotency_key": "idem-supply-001",
        "target_provider_reference": "provider-supply-001",
        "cancellation_reason": "FLOW_CANCELLED",
    },
}

RESULT_FIXTURES = {
    "wms.master_data.get_material@v1": {
        "material_code": "MAT-001",
        "material_name": "Material 001",
        "uom": "EA",
        "batch_managed": True,
        "serial_managed": False,
        "high_value": False,
    },
    "wms.master_data.list_materials@v1": {"items": [], "next_cursor": None},
    "wms.master_data.list_zones@v1": {"items": [], "next_cursor": None},
    "wms.master_data.list_locations@v1": {"items": [], "next_cursor": None},
    "wms.master_data.get_rack@v1": {
        "rack_id": "RACK-001",
        "rack_type": "FIVE_LAYER",
        "location_code": "LOC-A",
        "rack_face": "A",
        "capacity": 20,
        "status": "AVAILABLE",
    },
    "wms.master_data.list_racks@v1": {"items": [], "next_cursor": None},
    "wms.master_data.get_bin@v1": {
        "bin_id": "BIN-001",
        "rack_id": "RACK-001",
        "location_code": "LOC-A",
        "status": "AVAILABLE",
        "slots": [],
    },
    "wms.document.get_grn@v1": {
        "grn_id": "GRN-001",
        "po_number": "PO-001",
        "po_item": "10",
        "material_code": "MAT-001",
        "planned_quantity": "100",
        "received_quantity": "10",
        "remaining_quantity": "90",
        "quality_status": "RELEASED",
    },
    "wms.document.list_grn_packages@v1": {"items": [], "next_cursor": None},
    "wms.document.get_pick_order@v1": {
        "pick_order_id": "PICK-001",
        "wave_id": "WMS-WV-001",
        "status": "RELEASED",
        "priority": 1,
        "line_count": 1,
    },
    "wms.document.get_outbound_order@v1": {
        "outbound_order_id": "OUT-001",
        "status": "RELEASED",
        "destination_code": "LINE-001",
        "line_count": 1,
    },
    "wms.document.get_wave@v1": {
        "wave_id": "WMS-WV-001",
        "status": "RELEASED",
        "pick_order_ids": ["PICK-001"],
    },
    "wms.document.get_task_snapshot@v1": {
        "task_id": "TASK-001",
        "task_type": "RACK_TRANSPORT",
        "status": "PROCESSING",
        "provider_reference": "provider-task-001",
        "source_version": "1",
    },
    "wms.inventory.query_inventory@v1": {"items": [], "next_cursor": None, "source_version": "1"},
    "wms.inventory.get_reservation@v1": {
        "reservation_id": "RES-001",
        "status": "ACTIVE",
        "material_code": "MAT-001",
        "quantity": "10",
        "expires_at": "2026-07-29T01:00:00+00:00",
        "source_version": "1",
    },
    "wms.reconciliation.check_bin_drift@v1": {"items": [], "next_cursor": None, "source_version": "1"},
    "wms.reconciliation.check_rack_drift@v1": {"items": [], "next_cursor": None, "source_version": "1"},
    "wms.reconciliation.check_full_drift@v1": {"items": [], "next_cursor": None, "source_version": "1"},
    "wms.inventory.reserve_inventory@v1": {
        "dispatch_key": "dispatch-reserve-001",
        "provider_reference": "provider-reserve-001",
        "source_version": "1",
        "material_code": "MAT-001",
        "reservation_id": "RES-001",
        "reserved_quantity": "10",
        "expires_at": "2026-07-29T01:00:00+00:00",
    },
    "wms.inventory.release_reservation@v1": {
        "dispatch_key": "dispatch-release-001",
        "provider_reference": "provider-release-001",
        "source_version": "2",
        "reservation_id": "RES-001",
        "release_reference": "REL-001",
        "reservation_status": "RELEASED",
    },
    "wms.inventory.confirm_inbound@v1": {
        "dispatch_key": "dispatch-inbound-001",
        "provider_reference": "provider-inbound-001",
        "source_version": "2",
        "inbound_key": "IN-001",
        "wms_document_no": "DOC-IN-001",
        "inventory_source_version": "2",
    },
    "wms.inventory.confirm_outbound@v1": {
        "dispatch_key": "dispatch-outbound-001",
        "provider_reference": "provider-outbound-001",
        "source_version": "3",
        "outbound_key": "OUT-001",
        "issue_reference": "ISSUE-001",
        "inventory_source_version": "3",
    },
    "wms.inventory.transfer_inventory@v1": {
        "dispatch_key": "dispatch-transfer-001",
        "provider_reference": "provider-transfer-001",
        "source_version": "4",
        "transfer_key": "TRANSFER-001",
        "transfer_reference": "TR-001",
        "inventory_source_version": "4",
    },
    "wms.inventory.confirm_return_putaway@v1": {
        "dispatch_key": "dispatch-return-001",
        "provider_reference": "provider-return-001",
        "source_version": "5",
        "return_key": "RETURN-001",
        "return_reference": "RET-001",
        "new_pkg_id": "PKG-002",
        "inventory_source_version": "5",
    },
    "wms.fulfillment.notify_pkg_binding@v1": {
        "dispatch_key": "dispatch-binding-001",
        "provider_reference": "provider-binding-001",
        "source_version": "6",
        "pkg_id": "PKG-001",
        "binding_reference": "BIND-001",
    },
    "wms.fulfillment.request_rack_supply@v1": {
        "dispatch_key": "dispatch-supply-001",
        "provider_reference": "provider-supply-001",
        "source_version": "2",
        "station_code": "STATION-A",
        "rack_type": "FIVE_LAYER",
        "demand_generation": 1,
        "rack_id": "RACK-001",
        "final_station_code": "STATION-A",
        "arrival_relation": "AT_STATION",
        "task_outcome": "SUCCESS",
    },
    "wms.fulfillment.change_rack_face@v1": {
        "dispatch_key": "dispatch-face-001",
        "provider_reference": "provider-face-001",
        "source_version": "2",
        "rack_id": "RACK-001",
        "authorized_face": "B",
        "final_face": "B",
        "task_outcome": "SUCCESS",
    },
    "wms.fulfillment.publish_manual_task@v1": {
        "dispatch_key": "dispatch-manual-001",
        "provider_reference": "provider-manual-001",
        "source_version": "2",
        "manual_task_key": "MANUAL-001",
        "manual_task_reference": "MT-001",
        "publish_status": "PUBLISHED",
    },
    "wms.fulfillment.cancel_request@v1": {
        "dispatch_key": "dispatch-cancel-001",
        "provider_reference": "provider-cancel-001",
        "source_version": "2",
        "target_operation_identity": "wms.fulfillment.request_rack_supply@v1",
        "target_idempotency_key": "idem-supply-001",
        "target_provider_reference": "provider-supply-001",
        "disposition": "CANCELLED",
    },
}

REJECT_FIXTURES = {
    operation.identity: {
        "operation_identity": operation.identity,
        "reason_code": operation.reject_codes[0],
    }
    for operation in WMS_OPERATIONS
}

_operation_identities = tuple(operation.identity for operation in WMS_OPERATIONS)
IDENTITY_MISMATCH_FIXTURES = {
    identity: {
        "expected_operation_identity": identity,
        "actual_operation_identity": _operation_identities[(index + 1) % len(_operation_identities)],
    }
    for index, identity in enumerate(_operation_identities)
}

# pytest parameter generation imports this module before collection; any fixture
# identity/type drift therefore aborts collection instead of becoming a skipped case.
WMS_OPERATION_FIXTURE_MATRIX = build_operation_fixture_matrix(
    operations=WMS_OPERATIONS,
    request_fixtures=tuple(REQUEST_FIXTURES.items()),
    result_fixtures=tuple(RESULT_FIXTURES.items()),
    reject_fixtures=tuple(REJECT_FIXTURES.items()),
    identity_mismatch_fixtures=tuple(IDENTITY_MISMATCH_FIXTURES.items()),
)

__all__ = [
    "IDENTITY_MISMATCH_FIXTURES",
    "REJECT_FIXTURES",
    "REQUEST_FIXTURES",
    "RESULT_FIXTURES",
    "WMS_OPERATION_FIXTURE_MATRIX",
]
