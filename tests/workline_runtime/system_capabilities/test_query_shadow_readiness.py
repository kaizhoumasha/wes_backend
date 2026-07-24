from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest


def _versions(*, candidate: str = "policy.v2"):
    from src.app.runtime.system_capabilities.shadow_readiness import ShadowVersionSet

    return ShadowVersionSet(
        legacy_policy_version="policy.v1",
        candidate_policy_version=candidate,
        legacy_contract_version="inventory.v1",
        candidate_contract_version="inventory.v2",
        normalization_version="inventory-normalization.v1",
        evaluator_version="query-shadow-evaluator.v1",
    )


def _expected(
    comparison_key: str,
    observed_at: datetime,
    *,
    candidate: str = "policy.v2",
    eligible: bool = True,
):
    from src.app.runtime.system_capabilities.shadow_readiness import QueryShadowExpected

    return QueryShadowExpected(
        shadow_eligible=eligible,
        comparison_key=comparison_key,
        provider_profile_identity="wms.material-flow.production",
        operation_identity="wms.inventory.query_inventory@v1",
        versions=_versions(candidate=candidate),
        observed_at=observed_at,
        evidence_ref=f"query-evidence:{comparison_key}",
        input_hash="a" * 64,
        output_hash="b" * 64,
    )


def _draft(expected, *, difference_class: str = "MATCH", policy_ns: int = 1_000, query_ms: float = 10.0):
    from src.app.runtime.system_capabilities.shadow_readiness import (
        QueryShadowComparisonDraft,
        ShadowComparisonStatus,
        ShadowDecision,
        ShadowDifferenceClass,
    )

    legacy = ShadowDecision(action="ADMIT", reason="WMS_ADMITTED", error_class="NONE")
    candidate = legacy
    evaluator_error_code = None
    if difference_class == "ACTION_MISMATCH":
        candidate = ShadowDecision(action="HOLD", reason="WMS_TIMEOUT", error_class="TECHNICAL")
    elif difference_class == "EVALUATOR_ERROR":
        candidate = None
        evaluator_error_code = "SHADOW_POLICY_EVALUATION_FAILED"
    return QueryShadowComparisonDraft(
        expected=expected,
        comparison_status=ShadowComparisonStatus.STORED,
        legacy_decision=legacy,
        candidate_decision=candidate,
        difference_class=ShadowDifferenceClass(difference_class),
        divergence_diff=({"action": ["ADMIT", "HOLD"]} if difference_class == "ACTION_MISMATCH" else {}),
        legacy_policy_duration_ns=policy_ns,
        candidate_policy_duration_ns=policy_ns,
        query_end_to_end_duration_ms=query_ms,
        evaluator_error_code=evaluator_error_code,
    )


def test_shadow_expected_is_deterministic_and_embedded_in_query_evidence() -> None:
    from src.app.runtime.system_capabilities.evidence import QueryEvidence
    from src.app.runtime.system_capabilities.shadow_readiness import build_query_shadow_expected

    observed_at = datetime(2026, 7, 22, 8, 0, tzinfo=UTC)
    kwargs = {
        "attempt_id": "attempt-71",
        "capability_key": "wms.inventory.query_inventory",
        "provider_profile_identity": "wms.material-flow.production",
        "operation_identity": "wms.inventory.query_inventory@v1",
        "versions": _versions(),
        "observed_at": observed_at,
        "input_hash": "a" * 64,
        "output_hash": "b" * 64,
    }
    expected = build_query_shadow_expected(**kwargs)

    assert expected == build_query_shadow_expected(**kwargs)
    assert len(expected.comparison_key) == 64

    evidence = QueryEvidence(
        capability_key="wms.inventory.query_inventory",
        contract_version="v1",
        input_hash="a" * 64,
        output_hash="b" * 64,
        authority="WMS",
        source="material-flow",
        evidence_at=observed_at,
        source_version="inventory-42",
        admission_snapshot={"profile": "wms.material-flow.production"},
        summary={"outcome": {"kind": "success"}},
        shadow_expected=expected,
    )
    payload = evidence.payload()

    assert payload["shadow_expected"]["shadow_eligible"] is True
    assert payload["shadow_expected"]["comparison_key"] == expected.comparison_key
    assert payload["shadow_expected"]["versions"]["candidate_policy_version"] == "policy.v2"

    with pytest.raises(ValueError, match="hashes"):
        QueryEvidence(
            capability_key="wms.inventory.query_inventory",
            contract_version="v1",
            input_hash="f" * 64,
            output_hash="b" * 64,
            authority="WMS",
            source="material-flow",
            evidence_at=observed_at,
            source_version="inventory-42",
            admission_snapshot={"profile": "wms.material-flow.production"},
            summary={"outcome": {"kind": "success"}},
            shadow_expected=expected,
        )


def test_bounded_pure_evaluator_emits_only_references_hashes_and_controlled_diff() -> None:
    from src.app.runtime.system_capabilities.shadow_readiness import (
        BoundedQueryShadowEvaluator,
        QueryShadowEvaluationLimits,
        ShadowDecision,
        ShadowDifferenceClass,
    )

    expected = _expected("c" * 64, datetime(2026, 7, 22, tzinfo=UTC))
    evaluator = BoundedQueryShadowEvaluator(QueryShadowEvaluationLimits(max_decision_bytes=256, max_diff_entries=3))
    draft = evaluator.compare(
        expected=expected,
        legacy_decision=ShadowDecision(action="ADMIT", reason="WMS_ADMITTED", error_class="NONE"),
        candidate_decision=ShadowDecision(action="HOLD", reason="WMS_TIMEOUT", error_class="TECHNICAL"),
        legacy_policy_duration_ns=1_000,
        candidate_policy_duration_ns=1_100,
        query_end_to_end_duration_ms=12.5,
    )

    assert draft.difference_class is ShadowDifferenceClass.ACTION_MISMATCH
    assert draft.divergence_diff == {
        "action": ["ADMIT", "HOLD"],
        "error_class": ["NONE", "TECHNICAL"],
        "reason": ["WMS_ADMITTED", "WMS_TIMEOUT"],
    }
    serialized = draft.task_payload()
    assert serialized["evidence_ref"] == "query-evidence:" + "c" * 64
    assert not ({"payload", "normalized_input", "authority_snapshot", "request", "response"} & set(serialized))

    with pytest.raises(ValueError, match="divergence diff"):
        draft.model_copy(update={"divergence_diff": {"payload": ["secret", "secret"]}}).model_validate(
            draft.model_copy(update={"divergence_diff": {"payload": ["secret", "secret"]}}).model_dump()
        )


def test_shadow_persistence_payloads_serialize_timestamps_as_naive_utc() -> None:
    from src.app.runtime.system_capabilities.shadow_readiness import (
        QueryShadowReadinessApproval,
        QueryShadowReadinessPolicy,
        ReadinessApprovalDecision,
        build_query_shadow_readiness_report,
    )

    utc_plus_eight = timezone(timedelta(hours=8))
    expected = _expected("0" * 64, datetime(2026, 7, 22, 8, 0, tzinfo=utc_plus_eight))
    comparison = _draft(expected)
    report = build_query_shadow_readiness_report(
        provider_profile_identity=expected.provider_profile_identity,
        operation_identity=expected.operation_identity,
        expected_samples=[expected],
        comparisons=[comparison],
        generated_at=datetime(2026, 7, 23, 8, 0, tzinfo=utc_plus_eight),
        policy=QueryShadowReadinessPolicy(min_window_days=0, min_eligible_samples=1),
    )
    approval = QueryShadowReadinessApproval(
        report_id=report.report_id,
        decision=ReadinessApprovalDecision.GO,
        approved_by="migration-owner",
        approved_at=datetime(2026, 7, 23, 8, 1, tzinfo=utc_plus_eight),
    )

    assert comparison.task_payload()["observed_at"] == "2026-07-22T00:00:00"
    serialized_report = report.model_dump(mode="json")
    assert serialized_report["generated_at"] == "2026-07-23T00:00:00"
    assert serialized_report["window_started_at"] == "2026-07-22T00:00:00"
    assert serialized_report["window_ended_at"] == "2026-07-22T00:00:00"
    assert approval.model_dump(mode="json")["approved_at"] == "2026-07-23T00:01:00"


def test_bounded_pure_evaluator_failures_are_explicit_without_changing_production_decision() -> None:
    from src.app.runtime.system_capabilities.shadow_readiness import (
        BoundedQueryShadowEvaluator,
        QueryShadowEvaluationLimits,
        ShadowDecision,
        ShadowDifferenceClass,
    )

    expected = _expected("d" * 64, datetime(2026, 7, 22, tzinfo=UTC))
    production_decision = ShadowDecision(action="ADMIT", reason="WMS_ADMITTED", error_class="NONE")
    evaluator = BoundedQueryShadowEvaluator(QueryShadowEvaluationLimits(max_decision_bytes=64))

    draft = evaluator.compare(
        expected=expected,
        legacy_decision=production_decision,
        candidate_decision=ShadowDecision(action="X" * 100, reason="TOO_LARGE", error_class="CONTRACT"),
        legacy_policy_duration_ns=1_000,
        candidate_policy_duration_ns=1_000,
        query_end_to_end_duration_ms=12.5,
    )

    assert draft.difference_class is ShadowDifferenceClass.EVALUATOR_ERROR
    assert draft.legacy_decision == production_decision
    assert draft.candidate_decision is None
    assert draft.evaluator_error_code == "SHADOW_DECISION_BUDGET_EXCEEDED"


def test_readiness_uses_contiguous_suffix_and_resets_on_gap_version_and_evaluator_failure() -> None:
    from src.app.runtime.system_capabilities.shadow_readiness import (
        QueryShadowReadinessPolicy,
        ReadinessVerdict,
        build_query_shadow_readiness_report,
    )

    start = datetime(2026, 7, 1, tzinfo=UTC)
    expected = [
        _expected("1" * 64, start),
        _expected("2" * 64, start + timedelta(days=1)),  # stored gap
        _expected("3" * 64, start + timedelta(days=2), candidate="policy.v3"),  # version reset
        _expected("4" * 64, start + timedelta(days=3), candidate="policy.v3"),
        _expected("5" * 64, start + timedelta(days=10), candidate="policy.v3"),
    ]
    comparisons = [
        _draft(expected[0]),
        _draft(expected[2], difference_class="EVALUATOR_ERROR"),
        _draft(expected[3]),
        _draft(expected[4]),
    ]
    report = build_query_shadow_readiness_report(
        provider_profile_identity="wms.material-flow.production",
        operation_identity="wms.inventory.query_inventory@v1",
        expected_samples=expected,
        comparisons=comparisons,
        generated_at=start + timedelta(days=11),
        policy=QueryShadowReadinessPolicy(min_window_days=7, min_eligible_samples=2),
    )

    assert report.verdict is ReadinessVerdict.READY
    assert report.window_started_at == start + timedelta(days=3)
    assert report.eligible_samples == 2
    assert report.stored_comparisons == 2
    assert report.expected_stored_gap == 0
    assert set(report.reset_reasons) == {"EXPECTED_STORED_GAP", "EVALUATOR_ERROR", "VERSION_CHANGED"}


def test_readiness_report_is_content_addressed_and_separates_policy_and_query_p99() -> None:
    from src.app.runtime.system_capabilities.shadow_readiness import (
        QueryShadowReadinessApproval,
        QueryShadowReadinessPolicy,
        ReadinessApprovalDecision,
        ReadinessGateError,
        build_query_shadow_readiness_report,
        require_approved_readiness_report,
    )

    start = datetime(2026, 7, 1, tzinfo=UTC)
    expected = [_expected("6" * 64, start), _expected("7" * 64, start + timedelta(days=7))]
    comparisons = [
        _draft(expected[0], policy_ns=1_000, query_ms=10.0),
        _draft(expected[1], policy_ns=1_100, query_ms=25.0),
    ]
    kwargs = {
        "provider_profile_identity": "wms.material-flow.production",
        "operation_identity": "wms.inventory.query_inventory@v1",
        "expected_samples": expected,
        "comparisons": comparisons,
        "generated_at": start + timedelta(days=8),
        "policy": QueryShadowReadinessPolicy(
            min_window_days=7,
            min_eligible_samples=2,
            max_candidate_policy_p99_increase_ratio=0.10,
            max_query_end_to_end_p99_ms=30.0,
        ),
    }
    report = build_query_shadow_readiness_report(**kwargs)
    same = build_query_shadow_readiness_report(**kwargs)

    assert report.report_id == same.report_id
    assert report.legacy_policy_p99_ns == 1_100
    assert report.candidate_policy_p99_ns == 1_100
    assert report.query_end_to_end_p99_ms == 25.0
    approval = QueryShadowReadinessApproval(
        report_id=report.report_id,
        decision=ReadinessApprovalDecision.GO,
        approved_by="migration-owner",
        approved_at=start + timedelta(days=8, minutes=1),
    )
    require_approved_readiness_report(report=report, approval=approval)

    with pytest.raises(ReadinessGateError, match="report ID"):
        require_approved_readiness_report(
            report=report,
            approval=approval.model_copy(update={"report_id": "f" * 64}),
        )
    with pytest.raises(ReadinessGateError, match="content digest"):
        require_approved_readiness_report(
            report=report.model_copy(update={"eligible_samples": 9_999}),
            approval=approval,
        )


def test_current_gap_or_latency_failure_keeps_readiness_invalid() -> None:
    from src.app.runtime.system_capabilities.shadow_readiness import (
        QueryShadowReadinessPolicy,
        ReadinessVerdict,
        build_query_shadow_readiness_report,
    )

    start = datetime(2026, 7, 1, tzinfo=UTC)
    expected = [_expected("8" * 64, start), _expected("9" * 64, start + timedelta(days=7))]
    report = build_query_shadow_readiness_report(
        provider_profile_identity="wms.material-flow.production",
        operation_identity="wms.inventory.query_inventory@v1",
        expected_samples=expected,
        comparisons=[_draft(expected[0], query_ms=100.0)],
        generated_at=start + timedelta(days=8),
        policy=QueryShadowReadinessPolicy(
            min_window_days=7,
            min_eligible_samples=1,
            max_query_end_to_end_p99_ms=30.0,
        ),
    )

    assert report.verdict is ReadinessVerdict.INVALID
    assert report.expected_stored_gap == 1
    assert report.query_slo_passed is False


def test_duplicate_stored_comparison_invalidates_current_window() -> None:
    from src.app.runtime.system_capabilities.shadow_readiness import (
        QueryShadowReadinessPolicy,
        ReadinessVerdict,
        build_query_shadow_readiness_report,
    )

    start = datetime(2026, 7, 1, tzinfo=UTC)
    expected = _expected("e" * 64, start)
    duplicate = _draft(expected)
    report = build_query_shadow_readiness_report(
        provider_profile_identity=expected.provider_profile_identity,
        operation_identity=expected.operation_identity,
        expected_samples=[expected],
        comparisons=[duplicate, duplicate],
        generated_at=start + timedelta(days=1),
        policy=QueryShadowReadinessPolicy(min_window_days=0, min_eligible_samples=1),
    )

    assert report.verdict is ReadinessVerdict.INVALID
    assert "DUPLICATE_COMPARISON" in report.reset_reasons


def test_duplicate_expected_comparison_key_counts_as_one_independent_sample() -> None:
    from src.app.runtime.system_capabilities.shadow_readiness import (
        QueryShadowReadinessPolicy,
        ReadinessVerdict,
        build_query_shadow_readiness_report,
    )

    start = datetime(2026, 7, 1, tzinfo=UTC)
    expected = _expected("d" * 64, start)
    report = build_query_shadow_readiness_report(
        provider_profile_identity=expected.provider_profile_identity,
        operation_identity=expected.operation_identity,
        expected_samples=[expected, expected],
        comparisons=[_draft(expected)],
        generated_at=start + timedelta(days=1),
        policy=QueryShadowReadinessPolicy(min_window_days=0, min_eligible_samples=2),
    )

    assert report.verdict is ReadinessVerdict.NOT_READY
    assert report.eligible_samples == 1


def test_durably_marked_comparison_conflict_invalidates_current_window() -> None:
    from src.app.runtime.system_capabilities.shadow_readiness import (
        QueryShadowReadinessPolicy,
        ReadinessVerdict,
        ShadowComparisonStatus,
        build_query_shadow_readiness_report,
    )

    start = datetime(2026, 7, 1, tzinfo=UTC)
    expected = _expected("f" * 64, start)
    conflict = _draft(expected).model_copy(update={"comparison_status": ShadowComparisonStatus.CONFLICT})
    report = build_query_shadow_readiness_report(
        provider_profile_identity=expected.provider_profile_identity,
        operation_identity=expected.operation_identity,
        expected_samples=[expected],
        comparisons=[conflict],
        generated_at=start + timedelta(days=1),
        policy=QueryShadowReadinessPolicy(min_window_days=0, min_eligible_samples=1),
    )

    assert report.verdict is ReadinessVerdict.INVALID
    assert "COMPARISON_CONFLICT" in report.reset_reasons


def test_ineligible_version_change_resets_window_before_eligibility_filter() -> None:
    from src.app.runtime.system_capabilities.shadow_readiness import (
        QueryShadowReadinessPolicy,
        ReadinessVerdict,
        build_query_shadow_readiness_report,
    )

    start = datetime(2026, 7, 1, tzinfo=UTC)
    expected = [
        _expected("a" * 64, start, candidate="policy.v2"),
        _expected("b" * 64, start + timedelta(days=1), candidate="policy.v3", eligible=False),
        _expected("c" * 64, start + timedelta(days=7), candidate="policy.v2"),
    ]
    report = build_query_shadow_readiness_report(
        provider_profile_identity=expected[0].provider_profile_identity,
        operation_identity=expected[0].operation_identity,
        expected_samples=expected,
        comparisons=[_draft(expected[0]), _draft(expected[2])],
        generated_at=start + timedelta(days=8),
        policy=QueryShadowReadinessPolicy(min_window_days=7, min_eligible_samples=2),
    )

    assert report.verdict is ReadinessVerdict.NOT_READY
    assert report.window_started_at == start + timedelta(days=7)
    assert report.eligible_samples == 1
    assert report.excluded_samples == 1
    assert "VERSION_CHANGED" in report.reset_reasons
