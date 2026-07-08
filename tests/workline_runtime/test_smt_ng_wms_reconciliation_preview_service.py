"""Material-flow SMT/NG/WMS reconciliation preview capability 合同。"""

from __future__ import annotations

from src.app.runtime.capabilities.material_flow.smt_ng_wms_reconciliation_preview_service import (
    SmtNgWmsReconciliationPreviewService,
)


def test_reconciliation_preview_marks_conflicts_without_production_write_path() -> None:
    """WMS/NG 冲突场景只生成本机 preview，不推进生产写路径。"""

    service = SmtNgWmsReconciliationPreviewService()

    preview = service.preview_reconciliation_snapshot(
        {
            "scenario": "WMS_REJECT",
            "object_type": "PACKAGE",
            "object_key": "PKG-CAP001-LOT-A-001",
            "source_event_id": "mock-wms-reject",
            "source_version": "mock-wms.v2",
            "external_reference_type": "WMS_DOCUMENT",
            "external_reference_value": "DOC-001",
        }
    )

    assert preview["environment"] == "LOCAL_MOCK_ONLY"
    assert preview["production_write_path"] is False
    assert preview["legacy_plugin_entry_used"] is False
    assert preview["conflict_state"] == "RECONCILING"
    assert preview["requires_runtime_hold"] is True
    assert preview["allowed_next_effect_scope"] == "OBJECT_ONLY"
    assert preview["preserve_local_physical_fact"] is True
    assert preview["external_reference"] == {
        "type": "WMS_DOCUMENT",
        "value": "DOC-001",
    }


def test_reconciliation_preview_keeps_duplicate_callback_idempotent() -> None:
    """重复 callback 同 hash 语义必须是幂等合并，不创建 RuntimeHold。"""

    service = SmtNgWmsReconciliationPreviewService()

    preview = service.preview_reconciliation_snapshot(
        {
            "scenario": "DUPLICATE_CALLBACK",
            "object_type": "PACKAGE",
            "object_key": "PKG-CAP001-LOT-A-001",
            "source_event_id": "mock-duplicate",
            "source_version": "mock-wms.v2",
        }
    )

    assert preview["conflict_state"] == "IDEMPOTENT_DUPLICATE"
    assert preview["requires_runtime_hold"] is False
    assert preview["reconciliation_action"] == "MERGE_EVIDENCE_ONLY"


def test_reconciliation_preview_maps_expected_wave3_scenarios() -> None:
    """Wave3 mock 约定的冲突、乱序、拒绝、版本漂移场景必须显式映射。"""

    service = SmtNgWmsReconciliationPreviewService()
    expected_reasons = {
        "NG_EVIDENCE": "NG_EVIDENCE_LOCAL_STATE_MISMATCH",
        "MISSING_LOCAL_PHYSICAL_FACT": "MISSING_LOCAL_PHYSICAL_FACT",
        "WMS_REJECT": "WMS_REJECTED_LOCAL_FACT",
        "TARGET_BIN_WRITEBACK_FAILED": "TARGET_BIN_WRITEBACK_FAILED",
        "OUT_OF_ORDER_CALLBACK": "OUT_OF_ORDER_CALLBACK",
        "SOURCE_VERSION_DRIFT": "SOURCE_VERSION_DRIFT",
    }

    for scenario, expected_reason in expected_reasons.items():
        preview = service.preview_reconciliation_snapshot(
            {
                "scenario": scenario,
                "object_type": "PACKAGE",
                "object_key": f"PKG-{scenario}",
                "source_event_id": f"mock-{scenario.lower()}",
                "source_version": "mock-wms.v2",
            }
        )

        assert preview["conflict_state"] == "RECONCILING"
        assert preview["reason_code"] == expected_reason
        assert preview["reconciliation_action"] == "CREATE_RUNTIME_HOLD"
        assert preview["disallow_workline_wide_release"] is True


def test_runtime_hold_release_preview_is_scope_only() -> None:
    """RuntimeHold preview 只能释放声明 scope，不能顺手放行整线 effect。"""

    service = SmtNgWmsReconciliationPreviewService()

    preview = service.preview_runtime_hold_release(
        {
            "hold_id": "HOLD-MOCK-001",
            "scope_type": "OBJECT",
            "scope_key": "PKG-CAP001-LOT-A-001",
            "allowed_next_effect_scope": "OBJECT_ONLY",
            "requested_release_scope": "WORKLINE",
        }
    )

    assert preview["environment"] == "LOCAL_MOCK_ONLY"
    assert preview["production_write_path"] is False
    assert preview["legacy_plugin_entry_used"] is False
    assert preview["released_effect_scopes"] == ["OBJECT"]
    assert preview["blocked_effect_scopes"] == ["WORKLINE", "QUEUE", "DEVICE", "RESOURCE"]
    assert preview["requires_manual_review"] is True
    assert preview["disallow_workline_wide_release"] is True
