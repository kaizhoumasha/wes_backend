"""Material-flow sorter inbound runtime capability。

E03/E07 同步 WMS runtime 与 E11 新选箱合同都依赖后续 T5。旧异步 handler
已经关闭；本服务只保留不触发 WMS 外部副作用的分拣 join-gate 计划。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from src.app.runtime.capabilities.material_flow.runtime_identity import (
    MATERIAL_FLOW_IDEMPOTENCY_NAMESPACE,
    SORTER_INBOUND_RUNTIME_SOURCE,
)
from src.app.runtime.orchestration.runtime_intent import BlockScope, RuntimeIntent
from src.utils.value_normalization import coerce_string_value, require_text, string_list

if TYPE_CHECKING:
    from collections.abc import Mapping


SORTER_JOIN_CONDITION_ORDER = (
    "AUTHORIZED_BIN_RESOLVED",
    "TARGET_BIN_AT_WORK_POSITION",
    "TARGET_CELL_RESERVABLE",
    "CELL_RESERVATION_RESERVED",
    "WAITING_DEADLINE_DECLARED",
)
_T5_NOT_IMPLEMENTED = "T5 synchronous WMS runtime is not implemented"


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
        """E03/E07 同步执行器由 T5 提供；当前入口必须 fail closed。"""

        del payload
        raise RuntimeError(_T5_NOT_IMPLEMENTED)

    def build_sorter_inbound_plan(self, payload: Mapping[str, Any]) -> RuntimeCapabilityPlan:
        """构建不触发 WMS effect 的分拣机入库 join-gate 计划。"""

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

        idempotency_key = f"{MATERIAL_FLOW_IDEMPOTENCY_NAMESPACE}:{request_id}:sorter-ready"
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
                        "idempotency_key": idempotency_key,
                    },
                    idempotency_key=idempotency_key,
                )
            ],
            evidence={
                "request_id": request_id,
                "object_key": object_key,
                "condition_results": condition_results,
            },
        )

    def build_full_box_exchange_plan(self, payload: Mapping[str, Any]) -> RuntimeCapabilityPlan:
        """E11 新选箱合同由 T5 提供；当前入口必须 fail closed。"""

        del payload
        raise RuntimeError(_T5_NOT_IMPLEMENTED)


sorter_inbound_runtime_service = SorterInboundRuntimeService()

__all__ = [
    "RuntimeCapabilityPlan",
    "SorterInboundRuntimeService",
    "sorter_inbound_runtime_service",
]
