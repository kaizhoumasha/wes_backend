"""SMT/NG/WMS reconciliation shared policy."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.utils.value_normalization import coerce_string_value

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


def conflict_state(scenario: str) -> str:
    if scenario == "DUPLICATE_CALLBACK":
        return "IDEMPOTENT_DUPLICATE"
    if scenario == "OK":
        return "OK"
    return "RECONCILING"


def reason_code(scenario: str, state: str) -> str | None:
    if state == "OK":
        return None
    if state == "IDEMPOTENT_DUPLICATE":
        return "DUPLICATE_CALLBACK_SAME_HASH"
    return RECONCILING_REASON_BY_SCENARIO.get(scenario, "RECONCILIATION_REQUIRED")


def reconciliation_action(state: str) -> str:
    if state == "IDEMPOTENT_DUPLICATE":
        return "MERGE_EVIDENCE_ONLY"
    if state == "RECONCILING":
        return "CREATE_RUNTIME_HOLD"
    return "NONE"


def external_reference(payload: Mapping[str, Any]) -> dict[str, str] | None:
    reference_type = coerce_string_value(payload.get("external_reference_type"))
    reference_value = coerce_string_value(payload.get("external_reference_value"))
    if not reference_type and not reference_value:
        return None
    return {
        "type": reference_type,
        "value": reference_value,
    }
