"""Phase4 SMT/NG/WMS reconciliation preview capability.

本服务只表达开发/测试 MOCK 验收可用的对账语义:
- 不访问 DB / repository。
- 不发 WMS/NG/PDA effect。
- 不复用旧 plugin 入口。
- 不代表生产 callback cutover 或生产热路径接入。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping


LOCAL_PREVIEW_ENVIRONMENT = "LOCAL_MOCK_ONLY"
ALL_EFFECT_SCOPES = ["OBJECT", "WORKLINE", "QUEUE", "DEVICE", "RESOURCE"]
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


class SmtNgWmsReconciliationPreviewService:
    """Phase4 SMT/NG/WMS 本机 preview 服务。"""

    def preview_reconciliation_snapshot(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """预览 WMS/NG 对账快照，不推进任何生产写路径。"""

        scenario = _text(payload.get("scenario") or "OK").upper()
        conflict_state = self._conflict_state(scenario)
        requires_runtime_hold = conflict_state == "RECONCILING"
        reason_code = self._reason_code(scenario, conflict_state)
        return {
            **_preview_boundary(),
            "scenario": scenario,
            "object_type": payload.get("object_type", ""),
            "object_key": payload.get("object_key", ""),
            "source_event_id": payload.get("source_event_id", ""),
            "source_version": payload.get("source_version", "mock-wms.v1"),
            "external_reference": _external_reference(payload),
            "conflict_state": conflict_state,
            "reason_code": reason_code,
            "requires_runtime_hold": requires_runtime_hold,
            "allowed_next_effect_scope": "OBJECT_ONLY",
            "reconciliation_action": self._reconciliation_action(conflict_state),
            "preserve_local_physical_fact": scenario in {"WMS_REJECT", "TARGET_BIN_WRITEBACK_FAILED"},
            "disallow_workline_wide_release": requires_runtime_hold,
        }

    def preview_runtime_hold_release(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """预览 RuntimeHold scope-only release。"""

        allowed_scope = _text(payload.get("allowed_next_effect_scope") or "OBJECT_ONLY").upper()
        requested_scope = _text(payload.get("requested_release_scope") or allowed_scope).upper()
        released_effect_scopes = RELEASED_EFFECT_SCOPES_BY_ALLOWED_SCOPE.get(allowed_scope, ["OBJECT"])
        blocked_effect_scopes = [scope for scope in ALL_EFFECT_SCOPES if scope not in released_effect_scopes]
        return {
            **_preview_boundary(),
            "hold_id": payload.get("hold_id", ""),
            "scope_type": payload.get("scope_type", ""),
            "scope_key": payload.get("scope_key", ""),
            "allowed_next_effect_scope": allowed_scope,
            "requested_release_scope": requested_scope,
            "released_effect_scopes": released_effect_scopes,
            "blocked_effect_scopes": blocked_effect_scopes,
            "requires_manual_review": requested_scope != allowed_scope,
            "disallow_workline_wide_release": "WORKLINE" in blocked_effect_scopes,
        }

    def _conflict_state(self, scenario: str) -> str:
        if scenario == "DUPLICATE_CALLBACK":
            return "IDEMPOTENT_DUPLICATE"
        if scenario == "OK":
            return "OK"
        return "RECONCILING"

    def _reason_code(self, scenario: str, conflict_state: str) -> str | None:
        if conflict_state == "OK":
            return None
        if conflict_state == "IDEMPOTENT_DUPLICATE":
            return "DUPLICATE_CALLBACK_SAME_HASH"
        return RECONCILING_REASON_BY_SCENARIO.get(scenario, "RECONCILIATION_REQUIRED")

    def _reconciliation_action(self, conflict_state: str) -> str:
        if conflict_state == "IDEMPOTENT_DUPLICATE":
            return "MERGE_EVIDENCE_ONLY"
        if conflict_state == "RECONCILING":
            return "CREATE_RUNTIME_HOLD"
        return "NONE"


def _preview_boundary() -> dict[str, Any]:
    return {
        "environment": LOCAL_PREVIEW_ENVIRONMENT,
        "production_write_path": False,
        "legacy_plugin_entry_used": False,
    }


def _external_reference(payload: Mapping[str, Any]) -> dict[str, str] | None:
    reference_type = _text(payload.get("external_reference_type"))
    reference_value = _text(payload.get("external_reference_value"))
    if not reference_type and not reference_value:
        return None
    return {
        "type": reference_type,
        "value": reference_value,
    }


def _text(value: Any) -> str:
    return str(value or "")


smt_ng_wms_reconciliation_preview_service = SmtNgWmsReconciliationPreviewService()

__all__ = [
    "SmtNgWmsReconciliationPreviewService",
    "smt_ng_wms_reconciliation_preview_service",
]
