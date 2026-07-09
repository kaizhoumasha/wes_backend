"""Material-flow sorter inbound runtime capability.

本服务只构建 WES 自身运行闭环需要的 RuntimeIntent、effect contract 和 evidence。
外部 provider 的具体实现由部署 wiring 决定，本层只面向稳定 port contract。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from src.app.runtime.capabilities.material_flow.runtime_identity import (
    MATERIAL_FLOW_IDEMPOTENCY_NAMESPACE,
    SORTER_INBOUND_RUNTIME_SOURCE,
)
from src.app.runtime.orchestration.runtime_intent import BlockScope, RuntimeIntent
from src.utils.value_normalization import (
    coerce_string_value,
    positive_quantity,
    positive_timeout_seconds,
    require_text,
    require_text_any,
    string_list,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


SORTER_JOIN_CONDITION_ORDER = (
    "AUTHORIZED_BIN_RESOLVED",
    "TARGET_BIN_AT_WORK_POSITION",
    "TARGET_CELL_RESERVABLE",
    "CELL_RESERVATION_RESERVED",
    "WAITING_DEADLINE_DECLARED",
)


class RuntimeCapabilityPlan(BaseModel):
    """material-flow runtime capability 输出计划。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    provider_code: str = Field(min_length=1)
    contract_profile: str = "provider-contract"
    legacy_plugin_entry_used: bool = False
    reconciliation_required: bool = False
    reconciliation_action: str = "NONE"
    allowed_next_effect_scope: str | None = None
    intents: list[RuntimeIntent] = Field(default_factory=list)
    effect_contracts: dict[str, dict[str, Any]] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)


class SorterInboundRuntimeService:
    """Material-flow sorter inbound runtime capability builder。"""

    def build_rough_sorter_inbound_plan(self, payload: Mapping[str, Any]) -> RuntimeCapabilityPlan:
        """构建粗分机入库运行计划。"""

        request_id = require_text(payload, "request_id")
        correlation_id = require_text(payload, "correlation_id")
        provider_code = require_text(payload, "provider_code")
        object_key = require_text(payload, "object_key")
        bin_code = require_text_any(payload, "bin_code", "target_bin_code")
        bin_cell_index = require_text_any(payload, "bin_cell_index", "target_bin_cell_index", "target_cell_index")
        target_cell_code = require_text(payload, "target_cell_code")
        pkg_code = require_text(payload, "pkg_code")
        pallet_id = require_text(payload, "pallet_id")
        station_code = require_text(payload, "station_code")
        material_code = require_text(payload, "material_code")
        quantity = positive_quantity(payload.get("quantity"))
        warehouse_code = require_text(payload, "warehouse_code")

        reservation_payload = {
            "pkg_code": pkg_code,
            "bin_code": bin_code,
            "bin_cell_index": bin_cell_index,
            "bin_cell_code": target_cell_code,
            "material_identity_key": coerce_string_value(payload.get("material_identity_key")) or None,
            "correlation_id": correlation_id,
            "provider_code": provider_code,
            "source_event_id": coerce_string_value(payload.get("source_event_id")),
            "source_version": coerce_string_value(payload.get("source_version")),
            "evidence_json": {
                "request_id": request_id,
                "object_type": "PACKAGE",
                "object_key": object_key,
                "target_cell_code": target_cell_code,
            },
        }
        location_payload = {
            "object_type": "PACKAGE",
            "object_key": object_key,
            "location_scope": "CELL",
            "location_code": target_cell_code,
            "business_step": "LOCAL_PHYSICAL_FACT",
            "source": SORTER_INBOUND_RUNTIME_SOURCE,
            "provider_code": provider_code,
            "evidence_json": {
                "request_id": request_id,
                "provider_code": provider_code,
                "source_event_id": coerce_string_value(payload.get("source_event_id")),
                "source_version": coerce_string_value(payload.get("source_version")),
            },
            "correlation_id": correlation_id,
            "source_event_id": coerce_string_value(payload.get("source_event_id")),
            "source_version": coerce_string_value(payload.get("source_version")),
            "idempotency_key": f"{MATERIAL_FLOW_IDEMPOTENCY_NAMESPACE}:{request_id}:location-fact",
        }
        pkg_binding_payload = {
            "package_id": pkg_code,
            "pallet_id": pallet_id,
            "station_code": station_code,
            "provider_code": provider_code,
            "correlation_id": correlation_id,
            "idempotency_key": f"{MATERIAL_FLOW_IDEMPOTENCY_NAMESPACE}:{request_id}:pkg-binding",
        }
        inventory_payload = {
            "material_code": material_code,
            "quantity": quantity,
            "warehouse_code": warehouse_code,
            "provider_code": provider_code,
            "correlation_id": correlation_id,
            "idempotency_key": f"{MATERIAL_FLOW_IDEMPOTENCY_NAMESPACE}:{request_id}:inventory-confirm",
        }

        return RuntimeCapabilityPlan(
            provider_code=provider_code,
            intents=[
                RuntimeIntent.resource_reservation(
                    operation="CLAIM_BIN_CELL",
                    payload=reservation_payload,
                    idempotency_key=f"{MATERIAL_FLOW_IDEMPOTENCY_NAMESPACE}:{request_id}:cell-reservation",
                ),
                RuntimeIntent.resource_fact(
                    fact_type="RUNTIME_LOCATION_EVENT",
                    payload=location_payload,
                    idempotency_key=location_payload["idempotency_key"],
                ),
                _external_effect_intent(
                    dispatch_key=pkg_binding_payload["idempotency_key"],
                    target_code="WMS_FULFILLMENT",
                    port_method="WmsFulfillmentPort.notify_pkg_binding",
                    payload=pkg_binding_payload,
                ),
                _external_effect_intent(
                    dispatch_key=inventory_payload["idempotency_key"],
                    target_code="WMS_INVENTORY_TRANSACTION",
                    port_method="WmsInventoryTransactionPort.confirm_inbound",
                    payload=inventory_payload,
                ),
            ],
            effect_contracts={
                "WmsFulfillmentPort.notify_pkg_binding": {
                    "dispatch_key": pkg_binding_payload["idempotency_key"],
                    "payload": {
                        "package_id": pkg_code,
                        "pallet_id": pallet_id,
                        "station_code": station_code,
                    },
                },
                "WmsInventoryTransactionPort.confirm_inbound": {
                    "dispatch_key": inventory_payload["idempotency_key"],
                    "payload": {
                        "material_code": material_code,
                        "quantity": quantity,
                        "warehouse_code": warehouse_code,
                    },
                },
            },
            evidence={
                "request_id": request_id,
                "object_key": object_key,
                "target_cell_code": target_cell_code,
                "correlation_id": correlation_id,
            },
        )

    def build_sorter_inbound_plan(self, payload: Mapping[str, Any]) -> RuntimeCapabilityPlan:
        """构建分拣机入库运行计划。"""

        request_id = require_text(payload, "request_id")
        provider_code = require_text(payload, "provider_code")
        object_key = require_text(payload, "object_key")
        condition_results = {
            "AUTHORIZED_BIN_RESOLVED": coerce_string_value(payload.get("actual_scanned_bin_id"))
            in set(string_list(payload, "expected_authorized_bin_ids")),
            "TARGET_BIN_AT_WORK_POSITION": payload.get("target_bin_position_state") == "AT_WORK_POSITION",
            "TARGET_CELL_RESERVABLE": bool(payload.get("target_cell_reservable")),
            "CELL_RESERVATION_RESERVED": payload.get("cell_reservation_state") == "RESERVED",
            "WAITING_DEADLINE_DECLARED": bool(payload.get("waiting_deadline_declared")),
        }
        missing_conditions = [
            condition_name for condition_name in SORTER_JOIN_CONDITION_ORDER if not condition_results[condition_name]
        ]
        if missing_conditions:
            return RuntimeCapabilityPlan(
                provider_code=provider_code,
                reconciliation_required=True,
                allowed_next_effect_scope="OBJECT_ONLY",
                intents=[
                    RuntimeIntent.block(
                        scope=BlockScope.MATERIAL,
                        reason_code="SORTER_JOIN_GATE_NOT_SATISFIED",
                        message="Material-flow sorter inbound join gate is not satisfied",
                        payload={
                            "request_id": request_id,
                            "scope_type": "OBJECT",
                            "scope_key": object_key,
                            "missing_conditions": missing_conditions,
                            "condition_results": condition_results,
                        },
                    )
                ],
                evidence={
                    "request_id": request_id,
                    "object_key": object_key,
                    "condition_results": condition_results,
                },
            )

        return RuntimeCapabilityPlan(
            provider_code=provider_code,
            intents=[
                RuntimeIntent.resource_fact(
                    fact_type="RUNTIME_LOCATION_EVENT",
                    payload={
                        "object_type": "PACKAGE",
                        "object_key": object_key,
                        "location_scope": "WORK_POSITION",
                        "location_code": require_text(payload, "target_work_position_code"),
                        "business_step": "SORTER_READY_TO_DROP",
                        "source": SORTER_INBOUND_RUNTIME_SOURCE,
                        "provider_code": provider_code,
                        "evidence_json": {"request_id": request_id, "condition_results": condition_results},
                        "correlation_id": require_text(payload, "correlation_id"),
                        "idempotency_key": f"{MATERIAL_FLOW_IDEMPOTENCY_NAMESPACE}:{request_id}:sorter-ready",
                    },
                    idempotency_key=f"{MATERIAL_FLOW_IDEMPOTENCY_NAMESPACE}:{request_id}:sorter-ready",
                )
            ],
            evidence={
                "request_id": request_id,
                "object_key": object_key,
                "condition_results": condition_results,
            },
        )

    def build_full_box_exchange_plan(self, payload: Mapping[str, Any]) -> RuntimeCapabilityPlan:
        """构建满箱交换运行计划。"""

        request_id = require_text(payload, "request_id")
        correlation_id = require_text(payload, "correlation_id")
        provider_code = require_text(payload, "provider_code")
        rack_code = require_text(payload, "rack_code")
        empty_box_id = require_text(payload, "empty_box_id")
        full_box_id = require_text(payload, "full_box_id")
        full_box_set = set(string_list(payload, "full_box_object_keys"))
        sorting_candidate_object_keys = [
            object_key for object_key in string_list(payload, "remaining_object_keys") if object_key not in full_box_set
        ]
        operation_key = f"{MATERIAL_FLOW_IDEMPOTENCY_NAMESPACE}:{request_id}:full-box-exchange"

        return RuntimeCapabilityPlan(
            provider_code=provider_code,
            intents=[
                RuntimeIntent.rack_bin_exchange_request(
                    operation_type="FULL_BOX_EXCHANGE",
                    operation_key=operation_key,
                    rack_code=rack_code,
                    moves=[
                        {
                            "rack_code": rack_code,
                            "rack_side": coerce_string_value(payload.get("rack_side")),
                            "empty_box_id": empty_box_id,
                            "full_box_id": full_box_id,
                            "correlation_id": correlation_id,
                            "provider_code": provider_code,
                        }
                    ],
                    timeout_seconds=positive_timeout_seconds(payload.get("timeout_seconds")),
                )
            ],
            effect_contracts={
                "WmsFulfillmentPort.full_box_exchange": {
                    "dispatch_key": operation_key,
                    "payload": {
                        "rack_id": rack_code,
                        "empty_box_id": empty_box_id,
                        "full_box_id": full_box_id,
                    },
                }
            },
            evidence={
                "request_id": request_id,
                "batch_key": f"{rack_code}:{coerce_string_value(payload.get('rack_side'))}",
                "sorting_candidate_object_keys": sorting_candidate_object_keys,
            },
        )


def _external_effect_intent(
    *,
    dispatch_key: str,
    target_code: str,
    port_method: str,
    payload: dict[str, Any],
) -> RuntimeIntent:
    effect_payload = dict(payload)
    effect_payload["port_method"] = port_method
    return RuntimeIntent.external_request(
        dispatch_key=dispatch_key,
        target_code=target_code,
        source_system=coerce_string_value(payload.get("provider_code")),
        payload=effect_payload,
        timeout_seconds=30,
    )


sorter_inbound_runtime_service = SorterInboundRuntimeService()

__all__ = [
    "RuntimeCapabilityPlan",
    "SorterInboundRuntimeService",
    "sorter_inbound_runtime_service",
]
