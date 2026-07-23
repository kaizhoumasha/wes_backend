"""Material-flow sorter inbound runtime capability.

本服务只构建 WES 自身运行闭环需要的 RuntimeIntent、effect contract 和 evidence。
外部 provider 的具体实现由部署 wiring 决定，本层只面向稳定 port contract。
入库确认通过 typed EFFECT `wms.inventory.confirm_inbound@v1` 进入 T8 双账本。
料盘绑定通过 typed EFFECT `wms.fulfillment.notify_pkg_binding@v1` 进入同一双账本。
满箱交换通过 typed EFFECT `wms.fulfillment.full_box_exchange@v1` 进入同一双账本。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from src.app.runtime.capabilities.material_flow.runtime_identity import (
    MATERIAL_FLOW_IDEMPOTENCY_NAMESPACE,
    SORTER_INBOUND_RUNTIME_SOURCE,
)
from src.app.runtime.orchestration.runtime_intent import BlockScope, RuntimeIntent
from src.app.runtime.system_capabilities.wms.fulfillment.full_box_exchange.effect_contract import (
    FullBoxExchangeEffectAdmission,
    FullBoxExchangeEffectPrecondition,
)
from src.app.runtime.system_capabilities.wms.fulfillment.full_box_exchange.intent_adapter import (
    full_box_exchange_intent_adapter,
)
from src.app.runtime.system_capabilities.wms.fulfillment.notify_pkg_binding.effect_contract import (
    NotifyPackageBindingEffectAdmission,
    NotifyPackageBindingEffectPrecondition,
)
from src.app.runtime.system_capabilities.wms.fulfillment.notify_pkg_binding.intent_adapter import (
    notify_package_binding_intent_adapter,
)
from src.app.runtime.system_capabilities.wms.inventory.confirm_inbound.effect_contract import (
    ConfirmInboundEffectAdmission,
    ConfirmInboundEffectPrecondition,
)
from src.app.runtime.system_capabilities.wms.inventory.confirm_inbound.intent_adapter import (
    confirm_inbound_intent_adapter,
)
from src.app.wms_integration.ports.confirm_inbound_operation import ConfirmInboundOperationRequest
from src.app.wms_integration.ports.full_box_exchange_operation import FullBoxExchangeOperationRequest
from src.app.wms_integration.ports.notify_pkg_binding_operation import NotifyPackageBindingOperationRequest
from src.utils.value_normalization import (
    coerce_optional_int,
    coerce_string_value,
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
    evidence: dict[str, Any] = Field(default_factory=dict)


class SorterInboundRuntimeService:
    """Material-flow sorter inbound runtime capability builder。"""

    def build_rough_sorter_inbound_plan(self, payload: Mapping[str, Any]) -> RuntimeCapabilityPlan:
        """构建粗分机入库运行计划。"""

        request_id = require_text(payload.get("request_id"), "request_id")
        correlation_id = require_text(payload.get("correlation_id"), "correlation_id")
        provider_code = require_text(payload.get("provider_code"), "provider_code")
        object_key = require_text(payload.get("object_key"), "object_key")
        bin_code = require_text_any(payload, "bin_code", "target_bin_code")
        bin_cell_index = require_text_any(payload, "bin_cell_index", "target_bin_cell_index", "target_cell_index")
        target_cell_code = require_text(payload.get("target_cell_code"), "target_cell_code")
        pkg_code = require_text(payload.get("pkg_code"), "pkg_code")
        pallet_id = require_text(payload.get("pallet_id"), "pallet_id")
        station_code = require_text(payload.get("station_code"), "station_code")
        material_code = require_text(payload.get("material_code"), "material_code")
        try:
            quantity = Decimal(str(payload.get("quantity")))
            if not quantity.is_finite() or quantity <= 0:
                raise ValueError("quantity must be positive")
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("quantity must be positive") from exc
        warehouse_code = require_text(payload.get("warehouse_code"), "warehouse_code")

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
            "dispatch_key": f"wms-notify-pkg-binding:{provider_code}:{pkg_code}:{pallet_id}",
            "provider_code": provider_code,
            "package_id": pkg_code,
            "pallet_id": pallet_id,
            "station_code": station_code,
            "workline_id": coerce_optional_int(payload.get("workline_id")),
            "session_id": coerce_optional_int(payload.get("session_id")),
            "trace_id": coerce_string_value(payload.get("trace_id")) or None,
        }
        inventory_payload = {
            "dispatch_key": f"wms-confirm-inbound:{provider_code}:{object_key}",
            "inbound_key": object_key,
            "material_code": material_code,
            "quantity": quantity,
            "warehouse_code": warehouse_code,
            "owner_code": coerce_string_value(payload.get("owner_code")) or None,
            "lot_no": coerce_string_value(payload.get("lot_no")) or None,
            "workline_id": coerce_optional_int(payload.get("workline_id")),
            "session_id": coerce_optional_int(payload.get("session_id")),
            "trace_id": coerce_string_value(payload.get("trace_id")) or None,
        }
        fact_version = require_text(payload.get("source_version"), "source_version")
        package_binding_request = NotifyPackageBindingOperationRequest.model_validate(pkg_binding_payload)
        package_binding_admission = NotifyPackageBindingEffectAdmission(
            precondition=NotifyPackageBindingEffectPrecondition(
                package_id=pkg_code,
                pallet_id=pallet_id,
                local_physical_fact_recorded=True,
            ),
            fact_version=fact_version,
        )
        confirm_inbound_request = ConfirmInboundOperationRequest.model_validate(inventory_payload)
        confirm_inbound_admission = ConfirmInboundEffectAdmission(
            precondition=ConfirmInboundEffectPrecondition(
                inbound_key=object_key,
                local_physical_fact_recorded=True,
            ),
            fact_version=fact_version,
        )
        plugin_binding_id = coerce_optional_int(payload.get("plugin_binding_id"))
        plugin_binding_version = coerce_optional_int(payload.get("plugin_binding_version"))
        if plugin_binding_id is None or plugin_binding_version is None:
            raise ValueError("plugin_binding_id/plugin_binding_version is required")

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
                notify_package_binding_intent_adapter.build_intent(
                    package_binding_request,
                    admission=package_binding_admission,
                    binding_id=plugin_binding_id,
                    binding_version=plugin_binding_version,
                ),
                confirm_inbound_intent_adapter.build_intent(
                    confirm_inbound_request,
                    admission=confirm_inbound_admission,
                    binding_id=plugin_binding_id,
                    binding_version=plugin_binding_version,
                ),
            ],
            evidence={
                "request_id": request_id,
                "object_key": object_key,
                "target_cell_code": target_cell_code,
                "correlation_id": correlation_id,
            },
        )

    def build_sorter_inbound_plan(self, payload: Mapping[str, Any]) -> RuntimeCapabilityPlan:
        """构建分拣机入库运行计划。"""

        request_id = require_text(payload.get("request_id"), "request_id")
        provider_code = require_text(payload.get("provider_code"), "provider_code")
        object_key = require_text(payload.get("object_key"), "object_key")
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
                        "location_code": require_text(
                            payload.get("target_work_position_code"), "target_work_position_code"
                        ),
                        "business_step": "SORTER_READY_TO_DROP",
                        "source": SORTER_INBOUND_RUNTIME_SOURCE,
                        "provider_code": provider_code,
                        "evidence_json": {"request_id": request_id, "condition_results": condition_results},
                        "correlation_id": require_text(payload.get("correlation_id"), "correlation_id"),
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

        request_id = require_text(payload.get("request_id"), "request_id")
        correlation_id = require_text(payload.get("correlation_id"), "correlation_id")
        provider_code = require_text(payload.get("provider_code"), "provider_code")
        rack_code = require_text(payload.get("rack_code"), "rack_code")
        empty_box_id = require_text(payload.get("empty_box_id"), "empty_box_id")
        full_box_id = require_text(payload.get("full_box_id"), "full_box_id")
        full_box_set = set(string_list(payload, "full_box_object_keys"))
        sorting_candidate_object_keys = [
            object_key for object_key in string_list(payload, "remaining_object_keys") if object_key not in full_box_set
        ]
        dispatch_key = f"wms-full-box-exchange:{provider_code}:{rack_code}:{empty_box_id}:{full_box_id}"
        request = FullBoxExchangeOperationRequest(
            dispatch_key=dispatch_key,
            provider_code=provider_code,
            rack_id=rack_code,
            empty_box_id=empty_box_id,
            full_box_id=full_box_id,
            workline_id=coerce_optional_int(payload.get("workline_id")),
            session_id=coerce_optional_int(payload.get("session_id")),
            trace_id=coerce_string_value(payload.get("trace_id")) or None,
        )
        admission = FullBoxExchangeEffectAdmission(
            precondition=FullBoxExchangeEffectPrecondition(
                rack_id=rack_code,
                empty_box_id=empty_box_id,
                full_box_id=full_box_id,
                local_physical_fact_recorded=True,
            ),
            fact_version=require_text(payload.get("source_version"), "source_version"),
        )
        binding_id = coerce_optional_int(payload.get("plugin_binding_id"))
        binding_version = coerce_optional_int(payload.get("plugin_binding_version"))
        if binding_id is None or binding_version is None:
            raise ValueError("plugin_binding_id/plugin_binding_version is required")

        return RuntimeCapabilityPlan(
            provider_code=provider_code,
            intents=[
                full_box_exchange_intent_adapter.build_intent(
                    request,
                    admission=admission,
                    binding_id=binding_id,
                    binding_version=binding_version,
                )
            ],
            evidence={
                "request_id": request_id,
                "correlation_id": correlation_id,
                "batch_key": f"{rack_code}:{coerce_string_value(payload.get('rack_side'))}",
                "sorting_candidate_object_keys": sorting_candidate_object_keys,
            },
        )


sorter_inbound_runtime_service = SorterInboundRuntimeService()

__all__ = [
    "RuntimeCapabilityPlan",
    "SorterInboundRuntimeService",
    "sorter_inbound_runtime_service",
]
