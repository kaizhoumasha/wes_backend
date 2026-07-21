"""WMS Provider conformance 题库、纯评估器与 staging 报告合同。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.app.runtime.system_capabilities.wms.conformance_manifest import WMS_CONFORMANCE_MANIFEST
from src.app.runtime.system_capabilities.wms.provider_catalog import WMS_PROVIDER_PROFILES
from src.app.runtime.system_capabilities.wms.provider_conformance import (
    QUERY_INVENTORY_CONFORMANCE_CASES,
    ConformanceObservation,
    ConformanceTarget,
    build_wms_conformance_report,
    run_query_inventory_staging_live_conformance,
    verify_wms_conformance_report,
)

SANDBOX_PROFILE = WMS_PROVIDER_PROFILES["wms.2026-07-06.material-flow.sandbox"]
STAGING_PROFILE = WMS_PROVIDER_PROFILES["wms.2026-07-06.material-flow.staging"]
PRODUCTION_PROFILE = WMS_PROVIDER_PROFILES["wms.2026-07-06.material-flow.production"]
FIXTURE_DIGEST = "a" * 64
GENERATED_AT = datetime(2026, 7, 21, 8, 0, tzinfo=UTC)


def _matching_observations() -> tuple[ConformanceObservation, ...]:
    return tuple(
        ConformanceObservation(
            case_id=case.case_id,
            outcome_kind=case.outcome_kind,
            reason_code=case.reason_code,
            retryable=case.retryable,
            retry_after_seconds=case.retry_after_seconds,
            evidence_recorded=case.evidence_recorded,
            semantic_marker=case.semantic_marker,
        )
        for case in QUERY_INVENTORY_CONFORMANCE_CASES
    )


def test_manifest_query_requirement_is_the_single_complete_question_bank() -> None:
    query_requirement = next(
        item
        for item in WMS_CONFORMANCE_MANIFEST.operations
        if item.operation.identity == "wms.inventory.query_inventory@v1"
    )

    assert query_requirement.required_cases == tuple(case.case_id for case in QUERY_INVENTORY_CONFORMANCE_CASES)
    assert set(query_requirement.required_cases) == {
        "success",
        "empty",
        "missing_field",
        "invalid_decimal",
        "reject",
        "timeout",
        "rate_limit",
        "unavailable",
        "malformed",
        "pagination",
        "precision",
        "budget",
        "evidence_failure",
    }


@pytest.mark.parametrize(
    ("target", "profile"),
    [
        (ConformanceTarget.CI_ADAPTER, PRODUCTION_PROFILE),
        (ConformanceTarget.SIMULATOR, PRODUCTION_PROFILE),
        (ConformanceTarget.REPLAY, PRODUCTION_PROFILE),
        (ConformanceTarget.CI_ADAPTER, STAGING_PROFILE),
        (ConformanceTarget.STAGING_LIVE, SANDBOX_PROFILE),
        (ConformanceTarget.STAGING_LIVE, PRODUCTION_PROFILE),
    ],
)
def test_runner_fails_closed_before_non_staging_can_select_live_or_production_profile(target, profile) -> None:
    with pytest.raises(ValueError, match="environment"):
        build_wms_conformance_report(
            cases=QUERY_INVENTORY_CONFORMANCE_CASES,
            observations=_matching_observations(),
            target=target,
            profile=profile,
            fixture_digest=FIXTURE_DIGEST,
            endpoint_revision="staging-r42" if target is ConformanceTarget.STAGING_LIVE else None,
            generated_at=GENERATED_AT,
        )


def test_staging_live_report_is_explicit_immutable_deterministic_and_verifiable() -> None:
    kwargs = {
        "cases": QUERY_INVENTORY_CONFORMANCE_CASES,
        "observations": _matching_observations(),
        "target": ConformanceTarget.STAGING_LIVE,
        "profile": STAGING_PROFILE,
        "fixture_digest": FIXTURE_DIGEST,
        "endpoint_revision": "staging-r42",
        "generated_at": GENERATED_AT,
    }

    first = build_wms_conformance_report(**kwargs)
    second = build_wms_conformance_report(**kwargs)

    assert first == second
    assert first.passed is True
    assert first.profile_identity == STAGING_PROFILE.identity.identity
    assert first.endpoint_revision == "staging-r42"
    assert verify_wms_conformance_report(first.model_dump(mode="json")) == first
    with pytest.raises(ValidationError):
        first.report_digest = "b" * 64
    with pytest.raises(ValidationError, match="digest"):
        verify_wms_conformance_report({**first.model_dump(mode="json"), "report_digest": "b" * 64})


def test_report_schema_cannot_serialize_secrets_credentials_or_headers() -> None:
    report = build_wms_conformance_report(
        cases=QUERY_INVENTORY_CONFORMANCE_CASES,
        observations=_matching_observations(),
        target=ConformanceTarget.REPLAY,
        profile=SANDBOX_PROFILE,
        fixture_digest=FIXTURE_DIGEST,
        endpoint_revision=None,
        generated_at=GENERATED_AT,
    )
    serialized = report.model_dump_json().lower()

    assert "secret://" not in serialized
    assert "credential" not in serialized
    assert "header" not in serialized
    assert "signature" not in serialized


@pytest.mark.parametrize(
    ("reason_code", "semantic_marker"),
    [
        ("Bearer secret-material", "TECHNICAL_FAILURE"),
        ("X-WMS-Signature", "TECHNICAL_FAILURE"),
        ("WMS_UNAVAILABLE", "https://provider.invalid/raw"),
    ],
)
def test_observation_schema_rejects_raw_secret_header_or_url_values(reason_code, semantic_marker) -> None:
    with pytest.raises(ValidationError):
        ConformanceObservation(
            case_id="unavailable",
            outcome_kind="TECHNICAL_FAILURE",
            reason_code=reason_code,
            retryable=True,
            evidence_recorded=True,
            semantic_marker=semantic_marker,
        )


def test_report_rejects_missing_duplicate_or_unknown_case_observations() -> None:
    observations = _matching_observations()
    for invalid in (
        observations[:-1],
        (*observations, observations[0]),
        (*observations[:-1], observations[-1].model_copy(update={"case_id": "provider_extra"})),
    ):
        with pytest.raises(ValueError, match="exactly once"):
            build_wms_conformance_report(
                cases=QUERY_INVENTORY_CONFORMANCE_CASES,
                observations=invalid,
                target=ConformanceTarget.REPLAY,
                profile=SANDBOX_PROFILE,
                fixture_digest=FIXTURE_DIGEST,
                endpoint_revision=None,
                generated_at=GENERATED_AT,
            )


def test_report_builder_rejects_provider_attempt_to_override_or_shrink_core_bank() -> None:
    with pytest.raises(ValueError, match="cannot be overridden"):
        build_wms_conformance_report(
            cases=QUERY_INVENTORY_CONFORMANCE_CASES[:-1],
            observations=_matching_observations()[:-1],
            target=ConformanceTarget.REPLAY,
            profile=SANDBOX_PROFILE,
            fixture_digest=FIXTURE_DIGEST,
            endpoint_revision=None,
            generated_at=GENERATED_AT,
        )


@pytest.mark.asyncio
async def test_explicit_staging_entry_fails_closed_before_executor_and_runs_the_fixed_bank() -> None:
    observations = {item.case_id: item for item in _matching_observations()}
    calls: list[str] = []

    async def execute(case):
        calls.append(case.case_id)
        return observations[case.case_id]

    with pytest.raises(ValueError, match="staging environment"):
        await run_query_inventory_staging_live_conformance(
            profile=PRODUCTION_PROFILE,
            execute=execute,
            fixture_digest=FIXTURE_DIGEST,
            endpoint_revision="staging-r42",
            generated_at=GENERATED_AT,
        )
    assert calls == []

    for invalid_revision in (" ", "https://staging.invalid/v1", "X-WMS-Signature"):
        with pytest.raises(ValueError, match="endpoint revision"):
            await run_query_inventory_staging_live_conformance(
                profile=STAGING_PROFILE,
                execute=execute,
                fixture_digest=FIXTURE_DIGEST,
                endpoint_revision=invalid_revision,
                generated_at=GENERATED_AT,
            )
        assert calls == []

    profile_without_query = STAGING_PROFILE.model_copy(update={"bindings": (), "callbacks": ()})
    with pytest.raises(ValueError, match="query_inventory binding"):
        await run_query_inventory_staging_live_conformance(
            profile=profile_without_query,
            execute=execute,
            fixture_digest=FIXTURE_DIGEST,
            endpoint_revision="staging-r42",
            generated_at=GENERATED_AT,
        )
    assert calls == []

    report = await run_query_inventory_staging_live_conformance(
        profile=STAGING_PROFILE,
        execute=execute,
        fixture_digest=FIXTURE_DIGEST,
        endpoint_revision="staging-r42",
        generated_at=GENERATED_AT,
    )

    assert report.passed is True
    assert calls == [case.case_id for case in QUERY_INVENTORY_CONFORMANCE_CASES]
