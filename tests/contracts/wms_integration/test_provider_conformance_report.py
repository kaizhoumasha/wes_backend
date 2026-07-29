"""WMS Provider conformance 题库与无签名确定性报告合同。"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.app.runtime.system_capabilities.wms.conformance_manifest import WMS_CONFORMANCE_MANIFEST
from src.app.runtime.system_capabilities.wms.provider_catalog import WMS_PROVIDER_PROFILE
from src.app.runtime.system_capabilities.wms.provider_conformance import (
    QUERY_INVENTORY_CONFORMANCE_CASES,
    ConformanceObservation,
    ConformanceTarget,
    build_wms_conformance_report,
    verify_wms_conformance_report,
)

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


def _redigest_report_payload(payload: dict[str, object]) -> dict[str, object]:
    redigested = {key: value for key, value in payload.items() if key != "report_digest"}
    canonical = json.dumps(redigested, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return {**redigested, "report_digest": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}


def _build_report():
    return build_wms_conformance_report(
        cases=QUERY_INVENTORY_CONFORMANCE_CASES,
        observations=_matching_observations(),
        target=ConformanceTarget.REPLAY,
        fixture_digest=FIXTURE_DIGEST,
        generated_at=GENERATED_AT,
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


def test_report_is_bound_to_the_active_profile_and_deterministically_replayable() -> None:
    first = _build_report()
    second = _build_report()

    assert first == second
    assert first.profile_identity == WMS_PROVIDER_PROFILE.identity.identity
    assert first.suite_version == "wms-provider-q14-query-inventory.v1"
    assert first.passed is True
    assert verify_wms_conformance_report(first.model_dump(mode="json")) == first


def test_report_schema_has_no_staging_signature_trust_or_secret_surface() -> None:
    report = _build_report()
    fields = set(type(report).model_fields)
    serialized = report.model_dump_json().lower()

    assert fields.isdisjoint({"endpoint_revision", "staging_attestation", "signature", "signing_key_id"})
    assert "secret://" not in serialized
    assert "credential" not in serialized
    assert "header" not in serialized
    assert "signature" not in serialized
    assert "attestation" not in serialized


def test_report_verify_rejects_a_redigested_noncanonical_suite_digest() -> None:
    payload = _build_report().model_dump(mode="json")
    payload["suite_digest"] = "f" * 64

    with pytest.raises(ValueError, match="suite digest"):
        verify_wms_conformance_report(_redigest_report_payload(payload))


@pytest.mark.parametrize("mutation", ("reordered", "duplicated", "missing"))
def test_report_verify_rejects_redigested_case_identity_order_or_count_drift(mutation: str) -> None:
    payload = _build_report().model_dump(mode="json")
    cases = list(payload["cases"])
    if mutation == "reordered":
        cases[0], cases[1] = cases[1], cases[0]
    elif mutation == "duplicated":
        cases[-1] = cases[0]
    else:
        cases.pop()
    payload["cases"] = cases

    with pytest.raises(ValueError, match="case identity"):
        verify_wms_conformance_report(_redigest_report_payload(payload))


def test_report_verify_rejects_a_redigested_incorrect_case_verdict() -> None:
    payload = _build_report().model_dump(mode="json")
    cases = list(payload["cases"])
    cases[0] = {**cases[0], "semantic_marker": "EMPTY"}
    payload["cases"] = cases

    with pytest.raises(ValueError, match="case result"):
        verify_wms_conformance_report(_redigest_report_payload(payload))


def test_report_verify_rejects_a_redigested_non_active_profile() -> None:
    payload = _build_report().model_dump(mode="json")
    payload["profile_identity"] = "wms.unconfigured-provider"

    with pytest.raises(ValueError, match="profile identity"):
        verify_wms_conformance_report(_redigest_report_payload(payload))


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
                fixture_digest=FIXTURE_DIGEST,
                generated_at=GENERATED_AT,
            )


def test_report_builder_rejects_provider_attempt_to_override_or_shrink_core_bank() -> None:
    with pytest.raises(ValueError, match="cannot be overridden"):
        build_wms_conformance_report(
            cases=QUERY_INVENTORY_CONFORMANCE_CASES[:-1],
            observations=_matching_observations()[:-1],
            target=ConformanceTarget.REPLAY,
            fixture_digest=FIXTURE_DIGEST,
            generated_at=GENERATED_AT,
        )
