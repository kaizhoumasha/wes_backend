from __future__ import annotations

from datetime import UTC, datetime


def test_recorded_replay_strips_historical_shadow_expected_and_remains_legal_write_set() -> None:
    from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
        _write_set_from_recorded_replay,
    )
    from src.app.runtime.system_capabilities.evidence import QueryEvidence
    from src.app.runtime.system_capabilities.replay import RecordedReplayResolution
    from src.app.runtime.system_capabilities.shadow_readiness import (
        ShadowVersionSet,
        build_query_shadow_expected,
    )
    from src.app.runtime.workline_plugins.attempt_coordinator import (
        PluginWriteSetLimits,
        bound_attempt_write_set,
    )

    observed_at = datetime(2026, 7, 31, 23, 59, tzinfo=UTC)
    expected = build_query_shadow_expected(
        attempt_id="source-attempt-91",
        capability_key="wms.inventory.query_inventory",
        provider_profile_identity="wms.material-flow.production",
        operation_identity="wms.inventory.query_inventory@v1",
        versions=ShadowVersionSet(
            legacy_policy_version="policy.v1",
            candidate_policy_version="policy.v2",
            legacy_contract_version="inventory.v1",
            candidate_contract_version="inventory.v2",
            normalization_version="normalization.v1",
            evaluator_version="evaluator.v1",
        ),
        observed_at=observed_at,
        input_hash="a" * 64,
        output_hash="b" * 64,
    )
    evidence = QueryEvidence(
        capability_key="wms.inventory.query_inventory",
        contract_version="v1",
        input_hash=expected.input_hash,
        output_hash=expected.output_hash,
        authority="WMS",
        source="material-flow",
        evidence_at=observed_at,
        source_version="inventory-42",
        admission_snapshot={"profile": expected.provider_profile_identity},
        summary={"outcome": {"kind": "success"}},
        shadow_expected=expected,
    )

    replayed = _write_set_from_recorded_replay(
        RecordedReplayResolution(
            evidence=(evidence,),
            decision={"outcome_code": "ROUTE_A", "next_state": {}, "intents": []},
        ),
        fallback_state={},
    )
    bounded = bound_attempt_write_set(
        replayed,
        limits=PluginWriteSetLimits(),
        fallback_state={},
        allow_state_preservation=True,
    )

    assert replayed.evidence[0].shadow_expected is None
    assert replayed.shadow_comparisons == ()
    assert bounded.hold_reason is None
    assert bounded.evidence[0].shadow_expected is None
