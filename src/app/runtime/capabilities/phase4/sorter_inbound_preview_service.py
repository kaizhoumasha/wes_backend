"""Phase4 sorter inbound preview capability.

本服务只表达开发/测试 MOCK 验收可用的目标态入库语义:
- 不访问 DB / repository。
- 不发 WMS/ECS effect。
- 不复用旧 plugin 入口。
- 不代表 evidence profile 闭合。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping


LOCAL_PREVIEW_ENVIRONMENT = "LOCAL_MOCK_ONLY"

ROUGH_SORTER_ORDERED_STEPS = [
    "SCAN_AND_MEASURE",
    "WMS_GRN_BINDING_CHECK",
    "SOURCE_ARM_TO_CONVEYOR",
    "ROUGH_SORTER_TO_OUTBOUND",
    "CELL_RESERVATION",
    "OUTBOUND_ARM_TO_CELL",
    "LOCAL_PHYSICAL_FACT",
    "WMS_SYNC",
]

SORTER_INBOUND_ORDERED_STEPS = [
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

SORTER_JOIN_CONDITION_ORDER = [
    "AUTHORIZED_BIN_RESOLVED",
    "TARGET_BIN_AT_WORK_POSITION",
    "TARGET_CELL_RESERVABLE",
    "CELL_RESERVATION_RESERVED",
    "WAITING_DEADLINE_DECLARED",
]


class Phase4SorterInboundPreviewService:
    """Phase4 sorter inbound 本机 preview 服务。

    返回 payload 与 `tests/mock/phase4` 的 mock contract 对齐，但 service 本身不属于
    mock server；后续生产接线必须另走 production closure profile。
    """

    def preview_rough_sorter_inbound(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """预览粗分机正常流，拆分本地物理事实与 WMS 同步状态。"""

        local_physical_completed = bool(payload.get("local_physical_completed"))
        wms_pkg_binding_result = _text(payload.get("wms_pkg_binding_result") or "ACCEPTED").upper()
        wms_sync_state = "READY_TO_SYNC"
        business_completion_state = "LOCAL_PHYSICAL_COMPLETED"
        if local_physical_completed and wms_pkg_binding_result not in {"ACCEPTED", "CONFIRMED"}:
            wms_sync_state = "WMS_SYNC_PENDING"
            business_completion_state = "RECONCILING"

        return {
            **_preview_boundary(),
            "request_id": payload.get("request_id", ""),
            "object_key": payload.get("object_key", ""),
            "target_cell_code": payload.get("target_cell_code", ""),
            "ordered_steps": list(ROUGH_SORTER_ORDERED_STEPS),
            "local_position_state": "LOCAL_PHYSICAL_COMPLETED" if local_physical_completed else "PENDING",
            "wms_sync_state": wms_sync_state,
            "business_completion_state": business_completion_state,
            "preserve_local_physical_fact": local_physical_completed,
            "next_object_admission_allowed": True,
            "effect_ports": {
                "pkg_binding": "WmsFulfillmentPort.notify_pkg_binding",
                "inventory_transaction": "WmsInventoryTransactionPort.confirm_inbound",
            },
        }

    def preview_sorter_inbound(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """预览分拣机入库 join gate 与扫码平台预取策略。"""

        expected_authorized_bin_ids = set(_string_list(payload, "expected_authorized_bin_ids"))
        actual_scanned_bin_id = _text(payload.get("actual_scanned_bin_id"))
        condition_results = {
            "AUTHORIZED_BIN_RESOLVED": actual_scanned_bin_id in expected_authorized_bin_ids,
            "TARGET_BIN_AT_WORK_POSITION": payload.get("target_bin_position_state") == "AT_WORK_POSITION",
            "TARGET_CELL_RESERVABLE": bool(payload.get("target_cell_reservable")),
            "CELL_RESERVATION_RESERVED": payload.get("cell_reservation_state") == "RESERVED",
            "WAITING_DEADLINE_DECLARED": bool(payload.get("waiting_deadline_declared")),
        }
        missing_conditions = [
            condition_name for condition_name in SORTER_JOIN_CONDITION_ORDER if not condition_results[condition_name]
        ]
        capacity = _source_arm_prefetch_capacity(payload)
        manifest_validation = _source_arm_prefetch_manifest_validation(payload, capacity)
        scanner_platform_free = payload.get("scanner_platform_state") == "FREE"
        can_pick_next_material = (capacity > 0 and manifest_validation["allowed"]) or scanner_platform_free
        allowed = not missing_conditions

        return {
            **_preview_boundary(),
            "request_id": payload.get("request_id", ""),
            "prefetch_policy": {
                "source_arm_prefetch_capacity": capacity,
                "can_pick_next_material": can_pick_next_material,
                "requires_scanner_platform_free": capacity == 0,
            },
            "manifest_validation": manifest_validation,
            "ordered_steps": list(SORTER_INBOUND_ORDERED_STEPS),
            "join_gate": {
                "allowed": allowed,
                "condition_results": condition_results,
                "missing_conditions": missing_conditions,
            },
            "local_position_state": "LOCAL_PHYSICAL_COMPLETED" if allowed else "PENDING",
            "wms_sync_state": "READY_TO_SYNC" if allowed else "BLOCKED",
            "business_completion_state": "READY_TO_DROP" if allowed else "RECONCILING",
            "ng_route_state": "CLEAR" if allowed else "NG_OR_RUNTIME_HOLD",
            "runtime_hold_required": not allowed,
        }

    def preview_full_box_exchange(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """预览满箱交换前置分流。"""

        rack_code = _text(payload.get("rack_code"))
        rack_side = _text(payload.get("rack_side"))
        full_box_object_keys = _string_list(payload, "full_box_object_keys")
        full_box_set = set(full_box_object_keys)
        sorting_candidate_object_keys = [
            object_key
            for object_key in _string_list(payload, "remaining_object_keys")
            if object_key not in full_box_set
        ]
        exchange_required = bool(full_box_object_keys)

        return {
            **_preview_boundary(),
            "request_id": payload.get("request_id", ""),
            "fulfillment_action": "FULL_BOX_EXCHANGE" if exchange_required else "SORTER_STATION_ADMISSION",
            "batch_key": f"{rack_code}:{rack_side}",
            "rack_code": rack_code,
            "rack_side": rack_side,
            "exchange_zone": payload.get("exchange_zone", ""),
            "full_box_object_keys": full_box_object_keys,
            "sorting_candidate_object_keys": sorting_candidate_object_keys,
            "station_admission_blocked_until_exchange_completed": exchange_required,
            "box_level_inventory_transaction_required": exchange_required,
            "completion_policy": "CALLBACK_AND_RECONCILIATION_REQUIRED",
        }

    def preview_change_rack_face(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """预览 CHANGE_RACK_FACE 独立履约。"""

        return {
            **_preview_boundary(),
            "request_id": payload.get("request_id", ""),
            "parent_request_id": payload.get("parent_request_id", ""),
            "fulfillment_action": "CHANGE_RACK_FACE",
            "rack_code": _text(payload.get("rack_code")),
            "from_rack_side": _text(payload.get("from_rack_side")),
            "to_rack_side": _text(payload.get("to_rack_side")),
            "independent_fulfillment": True,
            "does_not_mark_full_box_exchange_completed": True,
            "completion_policy": "CALLBACK_AND_RECONCILIATION_REQUIRED",
        }

    def preview_ctu_batch(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """预览 CTU 父子批次查询视图。"""

        raw_child_items = payload.get("child_items")
        child_items = raw_child_items if isinstance(raw_child_items, list) else []
        sequence_nos = [_safe_int(item.get("sequence_no"), default=0) for item in child_items if isinstance(item, dict)]
        missing_resolved_placeholders = [
            _text(item.get("placeholder_key"))
            for item in child_items
            if isinstance(item, dict) and not item.get("resolved_bin_id")
        ]
        failed_child_placeholders = [
            _text(item.get("placeholder_key"))
            for item in child_items
            if isinstance(item, dict) and item.get("stage_status") != "COMPLETED"
        ]
        duplicate_sequence_nos = _duplicate_int_values(sequence_nos)
        has_child_issues = bool(missing_resolved_placeholders or failed_child_placeholders or duplicate_sequence_nos)
        parent_callback_state = _text(payload.get("parent_callback_state") or "PENDING").upper()
        parent_business_completed = parent_callback_state == "SUCCESS" and not has_child_issues
        operator_summary_state = "COMPLETED" if parent_business_completed else "RECONCILING"

        return {
            **_preview_boundary(),
            "parent_request_id": payload.get("parent_request_id", ""),
            "parent_callback_state": parent_callback_state,
            "parent_business_completed": parent_business_completed,
            "parent_projection_state": operator_summary_state,
            "query_view": {
                "child_count": len(child_items),
                "missing_resolved_placeholders": missing_resolved_placeholders,
                "duplicate_sequence_nos": duplicate_sequence_nos,
                "failed_child_placeholders": failed_child_placeholders,
                "operator_summary_state": operator_summary_state,
            },
        }


def _preview_boundary() -> dict[str, Any]:
    return {
        "environment": LOCAL_PREVIEW_ENVIRONMENT,
        "production_write_path": False,
        "legacy_plugin_entry_used": False,
    }


def _text(value: Any) -> str:
    return str(value or "")


def _string_list(payload: Mapping[str, Any], field_name: str) -> list[str]:
    raw_value = payload.get(field_name)
    if raw_value is None:
        return []
    if isinstance(raw_value, str):
        return [raw_value]
    if not isinstance(raw_value, list):
        return []
    return [str(item) for item in raw_value if str(item)]


def _source_arm_prefetch_capacity(payload: Mapping[str, Any]) -> int:
    manifest = payload.get("manifest")
    if not isinstance(manifest, dict):
        return 0
    return max(_safe_int(manifest.get("source_arm_prefetch_capacity"), default=0), 0)


def _source_arm_prefetch_manifest_validation(payload: Mapping[str, Any], capacity: int) -> dict[str, Any]:
    if capacity <= 0:
        return {"allowed": True, "errors": []}

    manifest = payload.get("manifest")
    manifest = manifest if isinstance(manifest, dict) else {}
    errors: list[str] = []
    ecs_capabilities = manifest.get("ecs_capabilities")
    if not isinstance(ecs_capabilities, list) or "SOURCE_ARM_PREFETCH" not in ecs_capabilities:
        errors.append("ECS_SOURCE_ARM_PREFETCH_CAPABILITY_REQUIRED")
    if _safe_int(manifest.get("prefetch_buffer_capacity"), default=0) < capacity:
        errors.append("PREFETCH_BUFFER_CAPACITY_TOO_SMALL")
    if _safe_int(manifest.get("prefetch_timeout_ms"), default=0) <= 0:
        errors.append("PREFETCH_TIMEOUT_REQUIRED")
    return {"allowed": not errors, "errors": errors}


def _safe_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _duplicate_int_values(values: list[int]) -> list[int]:
    seen: set[int] = set()
    duplicates: set[int] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


phase4_sorter_inbound_preview_service = Phase4SorterInboundPreviewService()

__all__ = [
    "LOCAL_PREVIEW_ENVIRONMENT",
    "Phase4SorterInboundPreviewService",
    "phase4_sorter_inbound_preview_service",
]
