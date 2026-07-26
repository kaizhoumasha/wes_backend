"""Material-flow SMT/NG/WMS reconciliation preview capability.

本服务只表达开发/测试 MOCK 验收可用的对账语义:
- 不访问 DB / repository。
- 不发 WMS/NG/PDA effect。
- 不复用旧 plugin 入口。
- 不代表 callback worker evidence 或 evidence profile 闭合。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.runtime.capabilities.material_flow.smt_ng_wms_reconciliation_policy import (
    ALL_EFFECT_SCOPES,
    RELEASED_EFFECT_SCOPES_BY_ALLOWED_SCOPE,
    conflict_state,
    external_reference,
    reason_code,
    reconciliation_action,
)
from src.utils.value_normalization import coerce_string_value

if TYPE_CHECKING:
    from collections.abc import Mapping


LOCAL_PREVIEW_ENVIRONMENT = "LOCAL_MOCK_ONLY"


class SmtNgWmsReconciliationPreviewService:
    """Material-flow SMT/NG/WMS 本机 preview 服务。"""

    def preview_reconciliation_snapshot(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """预览 WMS/NG 对账快照，不推进任何生产写路径。"""

        scenario = coerce_string_value(payload.get("scenario") or "OK").upper()
        state = conflict_state(scenario)
        requires_runtime_hold = state == "RECONCILING"
        return {
            **_preview_boundary(),
            "scenario": scenario,
            "object_type": payload.get("object_type", ""),
            "object_key": payload.get("object_key", ""),
            "source_event_id": payload.get("source_event_id", ""),
            "source_version": payload.get("source_version", "mock-wms.v1"),
            "external_reference": external_reference(payload),
            "conflict_state": state,
            "reason_code": reason_code(scenario, state),
            "requires_runtime_hold": requires_runtime_hold,
            "allowed_next_effect_scope": "OBJECT_ONLY",
            "reconciliation_action": reconciliation_action(state),
            "preserve_local_physical_fact": scenario in {"WMS_REJECT", "TARGET_BIN_WRITEBACK_FAILED"},
            "disallow_workline_wide_release": requires_runtime_hold,
        }

    def preview_runtime_hold_release(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """预览 RuntimeHold scope-only release。"""

        allowed_scope = coerce_string_value(payload.get("allowed_next_effect_scope") or "OBJECT_ONLY").upper()
        requested_scope = coerce_string_value(payload.get("requested_release_scope") or allowed_scope).upper()
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


def _preview_boundary() -> dict[str, Any]:
    return {
        "environment": LOCAL_PREVIEW_ENVIRONMENT,
        "production_write_path": False,
        "legacy_plugin_entry_used": False,
    }


smt_ng_wms_reconciliation_preview_service = SmtNgWmsReconciliationPreviewService()

__all__ = [
    "SmtNgWmsReconciliationPreviewService",
    "smt_ng_wms_reconciliation_preview_service",
]
