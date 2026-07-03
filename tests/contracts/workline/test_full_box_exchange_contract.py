"""BC-06 满箱交换前置分流 contract。

验收: 满箱/换箱/换架必须按外部履约 + 对账闭环建模, 不本地冒充完成。
满箱/换箱/换架必须按外部履约 + 对账闭环建模, 不本地冒充完成。

characterization 输入提取见 tests/characterization/workline_legacy/
test_full_box_exchange_characterization.py。
"""

from __future__ import annotations


def test_full_box_exchange_uses_fulfillment_and_reconciliation_contract():
    """目标态: 满箱/换箱/换架生成外部履约、等待回调、对账 evidence, 不本地冒充完成。

    Phase 3 收尾先锁住外部履约/evidence/reconciliation 语义。
    """

    from src.app.reconciliation.manager import ReconciliationConflictInput, ReconciliationManager
    from src.app.wms_integration.evidence import EvidenceEnvelope, ExternalReference
    from src.app.wms_integration.services.fulfillment_lifecycle import WmsFulfillmentLifecycleService
    from src.app.wms_integration.state_machine import FulfillmentEvent, FulfillmentState
    from src.utils.timezone import timezone

    now = timezone.now_for_db()
    lifecycle = WmsFulfillmentLifecycleService()
    record = lifecycle.open_request(
        request_id="fbx-001",
        fulfillment_kind="FULL_BOX_EXCHANGE",
        now=now,
        circuit_breaker_open=False,
    )
    sent = lifecycle.apply_event(record, FulfillmentEvent.DISPATCH_SENT, now=now)
    callback = lifecycle.apply_event(sent, FulfillmentEvent.CALLBACK_SUCCEEDED, now=now)
    evidence = EvidenceEnvelope(
        schema_version="evidence.v1",
        source_system="WMS",
        source_event_id="wms-fbx-001",
        source_version="wms-42",
        evidence_type="FULL_BOX_EXCHANGE_CALLBACK",
        occurred_at="2026-07-02T10:00:00Z",
        external_refs=[
            ExternalReference(
                system="WMS",
                object_type="FULL_BOX_EXCHANGE",
                code="fbx-001",
                schema_version="wms.full-box-exchange.v1",
                validated_at="2026-07-02T10:00:00Z",
                source_version="wms-42",
            )
        ],
        request_hash="b" * 64,
        payload_hash="a" * 64,
        payload={"rack_code": "RACK-001", "rack_side": "A"},
    )
    decision = ReconciliationManager().register_conflict(
        ReconciliationConflictInput(
            owner_domain="handling",
            owner_kind="FullBoxExchange",
            owner_id="fbx-001",
            conflict_kind="FULL_BOX_LOCAL_PROJECTION_CONFLICT",
            reason="external fulfillment succeeded but local projection has conflict",
            evidence_refs=["fulfillment:fbx-001", "evidence:wms-fbx-001"],
            detected_at=now,
        )
    )

    assert sent.state == FulfillmentState.SENT
    assert callback.state == FulfillmentState.SUCCEEDED
    assert callback.runtime_inbox_required is True
    assert evidence.evidence_type == "FULL_BOX_EXCHANGE_CALLBACK"
    assert decision.runtime_hold_required is True
    assert decision.allowed_next_effect_scope["owner_kind"] == "FullBoxExchange"
