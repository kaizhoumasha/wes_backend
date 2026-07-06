"""Phase4 SMT/NG/WMS reconciliation runtime capability 合同。

入站 callback 的上游边界是 RuntimeInbox；本测试只约束 provider-contract 输出。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.app.runtime.orchestration.runtime_intent import RuntimeIntentKind

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_reconciliation_runtime_turns_wms_reject_into_object_scope_hold() -> None:
    """WMS 拒绝真实 callback 时，runtime capability 必须生成 object scope hold 与 evidence。"""

    from src.app.runtime.capabilities.phase4.smt_ng_wms_reconciliation_runtime_service import (
        SmtNgWmsReconciliationRuntimeService,
    )

    service = SmtNgWmsReconciliationRuntimeService()

    plan = service.build_reconciliation_plan(
        {
            "scenario": "WMS_REJECT",
            "provider_code": "WMS-A",
            "correlation_id": "corr-reject-001",
            "object_type": "PACKAGE",
            "object_key": "PKG-CAP001-LOT-A-001",
            "source_event_id": "wms-reject-001",
            "source_version": "wms.v2",
            "external_reference_type": "WMS_DOCUMENT",
            "external_reference_value": "DOC-001",
        }
    )

    assert plan.provider_code == "WMS-A"
    assert plan.contract_profile == "provider-contract"
    assert plan.reconciliation_required is True
    assert plan.allowed_next_effect_scope == "OBJECT_ONLY"
    assert "mock" not in plan.model_dump_json().lower()
    assert "production" not in plan.model_dump_json().lower()
    assert [intent.kind for intent in plan.intents] == [RuntimeIntentKind.RESOURCE_FACT, RuntimeIntentKind.BLOCK]
    assert plan.intents[0].action == "RECONCILIATION_EVIDENCE"
    assert plan.intents[1].reason_code == "WMS_REJECTED_LOCAL_FACT"
    assert plan.intents[1].payload_json["scope_type"] == "OBJECT"
    assert plan.evidence["external_reference"] == {"type": "WMS_DOCUMENT", "value": "DOC-001"}
    assert plan.evidence["preserve_local_physical_fact"] is True


def test_reconciliation_runtime_merges_duplicate_callback_without_hold() -> None:
    """重复 callback 同 hash 时只合并 evidence，不创建 RuntimeHold。"""

    from src.app.runtime.capabilities.phase4.smt_ng_wms_reconciliation_runtime_service import (
        SmtNgWmsReconciliationRuntimeService,
    )

    service = SmtNgWmsReconciliationRuntimeService()

    plan = service.build_reconciliation_plan(
        {
            "scenario": "DUPLICATE_CALLBACK",
            "provider_code": "WMS-A",
            "correlation_id": "corr-dup-001",
            "object_type": "PACKAGE",
            "object_key": "PKG-DUP-001",
            "source_event_id": "wms-dup-001",
            "source_version": "wms.v2",
            "payload_hash": "sha256:abc",
        }
    )

    assert plan.reconciliation_required is False
    assert plan.reconciliation_action == "MERGE_EVIDENCE_ONLY"
    assert len(plan.intents) == 1
    assert plan.intents[0].kind == RuntimeIntentKind.RESOURCE_FACT
    assert plan.intents[0].payload_json["dedupe_result"] == "IDEMPOTENT_DUPLICATE"
    assert plan.effect_contracts == {}


def test_reconciliation_runtime_rejects_callback_without_source_event_id() -> None:
    """RuntimeInbox callback evidence 缺 source_event_id 时不能生成对账事实。"""

    from src.app.runtime.capabilities.phase4.smt_ng_wms_reconciliation_runtime_service import (
        SmtNgWmsReconciliationRuntimeService,
    )

    service = SmtNgWmsReconciliationRuntimeService()

    with pytest.raises(ValueError, match="source_event_id is required"):
        service.build_reconciliation_plan(
            {
                "scenario": "WMS_REJECT",
                "provider_code": "WMS-A",
                "correlation_id": "corr-reject-missing-source-event-001",
                "object_type": "PACKAGE",
                "object_key": "PKG-MISSING-SOURCE-EVENT-001",
                "source_version": "wms.v2",
            }
        )


def test_runtime_hold_release_plan_is_scope_only() -> None:
    """RuntimeHold 解除计划只释放声明 scope，不能顺手放行整线 effect。"""

    from src.app.runtime.capabilities.phase4.smt_ng_wms_reconciliation_runtime_service import (
        SmtNgWmsReconciliationRuntimeService,
    )

    service = SmtNgWmsReconciliationRuntimeService()

    plan = service.build_runtime_hold_release_plan(
        {
            "provider_code": "WMS-A",
            "hold_id": "HOLD-001",
            "scope_type": "OBJECT",
            "scope_key": "PKG-CAP001-LOT-A-001",
            "allowed_next_effect_scope": "OBJECT_ONLY",
            "requested_release_scope": "WORKLINE",
        }
    )

    assert plan.reconciliation_required is True
    assert plan.allowed_next_effect_scope == "OBJECT_ONLY"
    assert plan.evidence["released_effect_scopes"] == ["OBJECT"]
    assert plan.evidence["blocked_effect_scopes"] == ["WORKLINE", "QUEUE", "DEVICE", "RESOURCE"]
    assert plan.evidence["requires_manual_review"] is True


def test_runtime_capability_service_does_not_branch_on_external_environment() -> None:
    """runtime capability 不能根据外部 provider 是否模拟来选择业务路径。"""

    source = (
        REPO_ROOT
        / "src"
        / "app"
        / "runtime"
        / "capabilities"
        / "phase4"
        / "smt_ng_wms_reconciliation_runtime_service.py"
    ).read_text(encoding="utf-8")

    for forbidden in ("LOCAL_MOCK_ONLY", "production_write_path", "APP_ENV", "readiness_profile"):
        assert forbidden not in source
