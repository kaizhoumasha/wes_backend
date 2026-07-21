"""WMS Provider conformance 题库、纯评估器与 staging 报告合同。"""

from __future__ import annotations

import hashlib
import json
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


def _resign_report_payload(payload: dict[str, object]) -> dict[str, object]:
    resigned = {key: value for key, value in payload.items() if key != "report_digest"}
    canonical = json.dumps(resigned, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return {**resigned, "report_digest": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}


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
    ],
)
def test_runner_fails_closed_before_non_staging_can_select_live_or_production_profile(target, profile) -> None:
    with pytest.raises(ValueError, match="canonical author-time sandbox profile"):
        build_wms_conformance_report(
            cases=QUERY_INVENTORY_CONFORMANCE_CASES,
            observations=_matching_observations(),
            target=target,
            profile=profile,
            fixture_digest=FIXTURE_DIGEST,
            generated_at=GENERATED_AT,
        )


def test_staging_live_report_is_explicit_immutable_deterministic_and_verifiable() -> None:
    with pytest.raises(ValueError, match="attested live runner"):
        build_wms_conformance_report(
            cases=QUERY_INVENTORY_CONFORMANCE_CASES,
            observations=_matching_observations(),
            target=ConformanceTarget.STAGING_LIVE,
            profile=STAGING_PROFILE,
            fixture_digest=FIXTURE_DIGEST,
            generated_at=GENERATED_AT,
        )


@pytest.mark.parametrize(
    "endpoint_revision",
    (
        "release-42",
        "sk-live-0123456789",
        "api_key=0123456789",
        "Bearer 0123456789",
        "authorization-token",
        "0" * 64,
    ),
)
def test_endpoint_revision_requires_an_opaque_digest(endpoint_revision: str) -> None:
    kwargs = {
        "cases": QUERY_INVENTORY_CONFORMANCE_CASES,
        "observations": _matching_observations(),
        "target": ConformanceTarget.REPLAY,
        "profile": SANDBOX_PROFILE,
        "fixture_digest": FIXTURE_DIGEST,
        "endpoint_revision": endpoint_revision,
        "generated_at": GENERATED_AT,
    }
    with pytest.raises(TypeError, match="endpoint_revision"):
        build_wms_conformance_report(**kwargs)


def test_report_verify_rejects_a_resigned_noncanonical_suite_digest() -> None:
    report = build_wms_conformance_report(
        cases=QUERY_INVENTORY_CONFORMANCE_CASES,
        observations=_matching_observations(),
        target=ConformanceTarget.REPLAY,
        profile=SANDBOX_PROFILE,
        fixture_digest=FIXTURE_DIGEST,
        generated_at=GENERATED_AT,
    )
    payload = report.model_dump(mode="json")
    payload["suite_digest"] = "f" * 64

    with pytest.raises(ValueError, match="suite digest"):
        verify_wms_conformance_report(_resign_report_payload(payload))


@pytest.mark.parametrize("mutation", ("reordered", "duplicated", "missing"))
def test_report_verify_rejects_resigned_case_identity_order_or_count_drift(mutation: str) -> None:
    report = build_wms_conformance_report(
        cases=QUERY_INVENTORY_CONFORMANCE_CASES,
        observations=_matching_observations(),
        target=ConformanceTarget.REPLAY,
        profile=SANDBOX_PROFILE,
        fixture_digest=FIXTURE_DIGEST,
        generated_at=GENERATED_AT,
    )
    payload = report.model_dump(mode="json")
    cases = list(payload["cases"])
    if mutation == "reordered":
        cases[0], cases[1] = cases[1], cases[0]
    elif mutation == "duplicated":
        cases[-1] = cases[0]
    else:
        cases.pop()
    payload["cases"] = cases

    with pytest.raises(ValueError, match="case identity"):
        verify_wms_conformance_report(_resign_report_payload(payload))


def test_report_verify_rejects_a_resigned_incorrect_case_verdict() -> None:
    report = build_wms_conformance_report(
        cases=QUERY_INVENTORY_CONFORMANCE_CASES,
        observations=_matching_observations(),
        target=ConformanceTarget.REPLAY,
        profile=SANDBOX_PROFILE,
        fixture_digest=FIXTURE_DIGEST,
        generated_at=GENERATED_AT,
    )
    payload = report.model_dump(mode="json")
    cases = list(payload["cases"])
    cases[0] = {**cases[0], "semantic_marker": "EMPTY"}
    payload["cases"] = cases

    with pytest.raises(ValueError, match="case result"):
        verify_wms_conformance_report(_resign_report_payload(payload))


def test_report_verify_rejects_a_resigned_illegal_target_profile_environment() -> None:
    report = build_wms_conformance_report(
        cases=QUERY_INVENTORY_CONFORMANCE_CASES,
        observations=_matching_observations(),
        target=ConformanceTarget.REPLAY,
        profile=SANDBOX_PROFILE,
        fixture_digest=FIXTURE_DIGEST,
        generated_at=GENERATED_AT,
    )
    payload = report.model_dump(mode="json")
    payload["target"] = ConformanceTarget.STAGING_LIVE.value
    payload["endpoint_revision"] = "e" * 64

    with pytest.raises(ValueError, match="canonical author-time staging profile"):
        verify_wms_conformance_report(_resign_report_payload(payload))


def test_report_schema_cannot_serialize_secrets_credentials_or_headers() -> None:
    report = build_wms_conformance_report(
        cases=QUERY_INVENTORY_CONFORMANCE_CASES,
        observations=_matching_observations(),
        target=ConformanceTarget.REPLAY,
        profile=SANDBOX_PROFILE,
        fixture_digest=FIXTURE_DIGEST,
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
            generated_at=GENERATED_AT,
        )
