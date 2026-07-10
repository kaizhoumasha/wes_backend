"""Material-flow SMT/NG/WMS reconciliation runtime capability.

入站 callback 上游必须先经 RuntimeInbox normalize/worker，本服务只构建
provider-contract 对账 evidence 与 hold plan。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.runtime.capabilities.material_flow.runtime_identity import MATERIAL_FLOW_IDEMPOTENCY_NAMESPACE
from src.app.runtime.capabilities.material_flow.sorter_inbound_runtime_service import RuntimeCapabilityPlan
from src.app.runtime.orchestration.runtime_intent import BlockScope, RuntimeIntent
from src.utils.value_normalization import coerce_string_value, require_text

if TYPE_CHECKING:
    from collections.abc import Mapping


ALL_EFFECT_SCOPES = ("OBJECT", "WORKLINE", "QUEUE", "DEVICE", "RESOURCE")
RELEASED_EFFECT_SCOPES_BY_ALLOWED_SCOPE = {
    "OBJECT_ONLY": ["OBJECT"],
    "QUEUE_ONLY": ["QUEUE"],
    "DEVICE_ONLY": ["DEVICE"],
    "RESOURCE_ONLY": ["RESOURCE"],
    "WORKLINE": ["WORKLINE"],
}
RECONCILING_REASON_BY_SCENARIO = {
    "NG_EVIDENCE": "NG_EVIDENCE_LOCAL_STATE_MISMATCH",
    "MISSING_LOCAL_PHYSICAL_FACT": "MISSING_LOCAL_PHYSICAL_FACT",
    "WMS_REJECT": "WMS_REJECTED_LOCAL_FACT",
    "TARGET_BIN_WRITEBACK_FAILED": "TARGET_BIN_WRITEBACK_FAILED",
    "OUT_OF_ORDER_CALLBACK": "OUT_OF_ORDER_CALLBACK",
    "SOURCE_VERSION_DRIFT": "SOURCE_VERSION_DRIFT",
}


class SmtNgWmsReconciliationRuntimeService:
    """Material-flow SMT/NG/WMS 对账 runtime capability builder。"""

    def build_reconciliation_plan(self, payload: Mapping[str, Any]) -> RuntimeCapabilityPlan:
        """构建 callback 对账运行计划。"""

        provider_code = require_text(payload.get("provider_code"), "provider_code")
        scenario = coerce_string_value(payload.get("scenario") or "OK").upper()
        conflict_state = _conflict_state(scenario)
        reason_code = _reason_code(scenario, conflict_state)
        source_event_id = require_text(payload.get("source_event_id"), "source_event_id")
        source_version = require_text(payload.get("source_version"), "source_version")
        object_type = require_text(payload.get("object_type"), "object_type")
        object_key = require_text(payload.get("object_key"), "object_key")
        correlation_id = require_text(payload.get("correlation_id"), "correlation_id")
        external_reference = _external_reference(payload)
        evidence_payload = {
            "scenario": scenario,
            "conflict_state": conflict_state,
            "reason_code": reason_code,
            "object_type": object_type,
            "object_key": object_key,
            "correlation_id": correlation_id,
            "provider_code": provider_code,
            "source_event_id": source_event_id,
            "source_version": source_version,
            "external_reference": external_reference,
            "payload_hash": coerce_string_value(payload.get("payload_hash")),
            "dedupe_result": conflict_state,
        }
        intents = [
            RuntimeIntent.resource_fact(
                fact_type="RECONCILIATION_EVIDENCE",
                payload=evidence_payload,
                idempotency_key=f"{MATERIAL_FLOW_IDEMPOTENCY_NAMESPACE}:{source_event_id}:reconciliation-evidence",
            )
        ]
        reconciliation_required = conflict_state == "RECONCILING"
        if reconciliation_required:
            intents.append(
                RuntimeIntent.block(
                    scope=BlockScope.MATERIAL,
                    reason_code=reason_code or "RECONCILIATION_REQUIRED",
                    message="Material-flow reconciliation requires manual or worker resolution",
                    payload={
                        "scope_type": "OBJECT",
                        "scope_key": object_key,
                        "object_type": object_type,
                        "correlation_id": correlation_id,
                        "provider_code": provider_code,
                        "source_event_id": source_event_id,
                        "source_version": source_version,
                        "allowed_next_effect_scope": "OBJECT_ONLY",
                        "external_reference": external_reference,
                    },
                )
            )

        return RuntimeCapabilityPlan(
            provider_code=provider_code,
            reconciliation_required=reconciliation_required,
            allowed_next_effect_scope="OBJECT_ONLY" if reconciliation_required else None,
            intents=intents,
            evidence={
                "scenario": scenario,
                "conflict_state": conflict_state,
                "reason_code": reason_code,
                "external_reference": external_reference,
                "preserve_local_physical_fact": scenario in {"WMS_REJECT", "TARGET_BIN_WRITEBACK_FAILED"},
            },
            reconciliation_action=_reconciliation_action(conflict_state),
        )

    def build_runtime_hold_release_plan(self, payload: Mapping[str, Any]) -> RuntimeCapabilityPlan:
        """构建 RuntimeHold scope-only release 运行计划。"""

        provider_code = require_text(payload.get("provider_code"), "provider_code")
        allowed_scope = coerce_string_value(payload.get("allowed_next_effect_scope") or "OBJECT_ONLY").upper()
        requested_scope = coerce_string_value(payload.get("requested_release_scope") or allowed_scope).upper()
        released_effect_scopes = RELEASED_EFFECT_SCOPES_BY_ALLOWED_SCOPE.get(allowed_scope, ["OBJECT"])
        blocked_effect_scopes = [scope for scope in ALL_EFFECT_SCOPES if scope not in released_effect_scopes]
        requires_manual_review = requested_scope != allowed_scope
        return RuntimeCapabilityPlan(
            provider_code=provider_code,
            reconciliation_required=requires_manual_review,
            allowed_next_effect_scope=allowed_scope,
            evidence={
                "hold_id": require_text(payload.get("hold_id"), "hold_id"),
                "scope_type": require_text(payload.get("scope_type"), "scope_type"),
                "scope_key": require_text(payload.get("scope_key"), "scope_key"),
                "requested_release_scope": requested_scope,
                "released_effect_scopes": released_effect_scopes,
                "blocked_effect_scopes": blocked_effect_scopes,
                "requires_manual_review": requires_manual_review,
            },
        )


def _conflict_state(scenario: str) -> str:
    if scenario == "DUPLICATE_CALLBACK":
        return "IDEMPOTENT_DUPLICATE"
    if scenario == "OK":
        return "OK"
    return "RECONCILING"


def _reason_code(scenario: str, conflict_state: str) -> str | None:
    if conflict_state == "OK":
        return None
    if conflict_state == "IDEMPOTENT_DUPLICATE":
        return "DUPLICATE_CALLBACK_SAME_HASH"
    return RECONCILING_REASON_BY_SCENARIO.get(scenario, "RECONCILIATION_REQUIRED")


def _reconciliation_action(conflict_state: str) -> str:
    if conflict_state == "IDEMPOTENT_DUPLICATE":
        return "MERGE_EVIDENCE_ONLY"
    if conflict_state == "RECONCILING":
        return "CREATE_RUNTIME_HOLD"
    return "NONE"


def _external_reference(payload: Mapping[str, Any]) -> dict[str, str] | None:
    reference_type = coerce_string_value(payload.get("external_reference_type"))
    reference_value = coerce_string_value(payload.get("external_reference_value"))
    if not reference_type and not reference_value:
        return None
    return {
        "type": reference_type,
        "value": reference_value,
    }


smt_ng_wms_reconciliation_runtime_service = SmtNgWmsReconciliationRuntimeService()

__all__ = [
    "SmtNgWmsReconciliationRuntimeService",
    "smt_ng_wms_reconciliation_runtime_service",
]
