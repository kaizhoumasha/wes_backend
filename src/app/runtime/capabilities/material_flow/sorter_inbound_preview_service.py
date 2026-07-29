"""Material-flow sorter inbound preview capability.

本服务只表达开发/测试 MOCK 验收可用的目标态入库语义:
- 不访问 DB / repository。
- 不发 WMS/ECS effect。
- 不复用旧 plugin 入口。
- 不代表 evidence profile 闭合。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.wms_integration.ports.fulfillment_operations import NOTIFY_PKG_BINDING
from src.app.wms_integration.ports.inventory_operations import CONFIRM_INBOUND
from src.utils.value_normalization import coerce_string_value, string_list

if TYPE_CHECKING:
    from collections.abc import Mapping


LOCAL_PREVIEW_ENVIRONMENT = "LOCAL_MOCK_ONLY"
CONFIRM_INBOUND_IDENTITY = CONFIRM_INBOUND.identity
NOTIFY_PACKAGE_BINDING_IDENTITY = NOTIFY_PKG_BINDING.identity

ROUGH_SORTER_ORDERED_STEPS = [
    "SCAN_AND_MEASURE",
    "SOURCE_ARM_TO_CONVEYOR",
    "ROUGH_SORTER_TO_OUTBOUND",
    "CELL_RESERVATION",
    "OUTBOUND_ARM_TO_CELL",
    "LOCAL_PHYSICAL_FACT",
    "WMS_SYNC",
]

SORTER_INBOUND_ORDERED_STEPS = [
    "STATION_ADMISSION",
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


class SorterInboundPreviewService:
    """Material-flow sorter inbound 本机 preview 服务。

    返回 payload 与 `tests/mock/material_flow` 的 mock contract 对齐，但 service 本身不属于
    mock server；后续生产接线必须另走 production closure profile。
    """

    def preview_rough_sorter_inbound(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """预览粗分机正常流，拆分本地物理事实与 WMS 同步状态。"""

        local_physical_completed = bool(payload.get("local_physical_completed"))
        return {
            **_preview_boundary(),
            "request_id": payload.get("request_id", ""),
            "object_key": payload.get("object_key", ""),
            "target_cell_code": payload.get("target_cell_code", ""),
            "ordered_steps": list(ROUGH_SORTER_ORDERED_STEPS),
            "local_position_state": "LOCAL_PHYSICAL_COMPLETED" if local_physical_completed else "PENDING",
            "wms_sync_state": "READY_TO_SYNC",
            "business_completion_state": "LOCAL_PHYSICAL_COMPLETED",
            "preserve_local_physical_fact": local_physical_completed,
            "next_object_admission_allowed": True,
            "effect_ports": {
                "pkg_binding": NOTIFY_PACKAGE_BINDING_IDENTITY,
                "inventory_transaction": CONFIRM_INBOUND_IDENTITY,
            },
        }

    def preview_sorter_inbound(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """预览分拣机入库 join gate 与南向 PICK ACK 因果链。"""

        expected_authorized_bin_ids = set(string_list(payload, "expected_authorized_bin_ids"))
        actual_scanned_bin_id = coerce_string_value(payload.get("actual_scanned_bin_id"))
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
        allowed = not missing_conditions

        return {
            **_preview_boundary(),
            "request_id": payload.get("request_id", ""),
            # 平台空闲与双臂防呆由 PLC/机器人保证；WES 仅依据南向 PICK ACK 触发下一次北向取料。
            "next_northbound_pick_triggered": bool(payload.get("southbound_pick_acknowledged")),
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

        rack_code = coerce_string_value(payload.get("rack_code"))
        rack_side = coerce_string_value(payload.get("rack_side"))
        full_box_object_keys = string_list(payload, "full_box_object_keys")
        full_box_set = set(full_box_object_keys)
        sorting_candidate_object_keys = [
            object_key for object_key in string_list(payload, "remaining_object_keys") if object_key not in full_box_set
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
        }


def _preview_boundary() -> dict[str, Any]:
    return {
        "environment": LOCAL_PREVIEW_ENVIRONMENT,
        "production_write_path": False,
        "legacy_plugin_entry_used": False,
    }


sorter_inbound_preview_service = SorterInboundPreviewService()

__all__ = [
    "LOCAL_PREVIEW_ENVIRONMENT",
    "SorterInboundPreviewService",
    "sorter_inbound_preview_service",
]
