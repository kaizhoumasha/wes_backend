"""WMS 全工厂 Provider conformance 确定性发布报告合同。"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.app.runtime.system_capabilities.wms.conformance_manifest import build_wms_conformance_manifest
from src.app.runtime.system_capabilities.wms.provider_conformance import (
    WMS_PROVIDER_CONFORMANCE_CASES,
    ConformanceTarget,
    OperationConformanceObservation,
    build_wms_conformance_report,
    build_wms_release_conformance_report,
    verify_wms_conformance_report,
    verify_wms_release_conformance_report,
)
from src.app.wms_integration.operation_registry import WMS_OPERATIONS
from src.app.wms_integration.provider_manifest import conformance_cases_for_operation
from tests.support.wms_provider_conformance import WMS_CONFORMANCE_COMPILED_PROFILE

FIXTURE_DIGEST = "a" * 64
GENERATED_AT = datetime(2026, 7, 21, 8, 0, tzinfo=UTC)
REPORT_METADATA = {
    "wms_build_version": "wms-build-2026.07.30",
    "responsible_person": "WMS-OWNER-001",
    "execution_safety_confirmed": True,
}


def _matching_observations() -> tuple[OperationConformanceObservation, ...]:
    return tuple(
        OperationConformanceObservation.model_validate(case.model_dump(mode="json"))
        for case in WMS_PROVIDER_CONFORMANCE_CASES
    )


def _redigest_report_payload(payload: dict[str, object]) -> dict[str, object]:
    redigested = {key: value for key, value in payload.items() if key != "report_digest"}
    canonical = json.dumps(redigested, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return {**redigested, "report_digest": hashlib.sha256(canonical.encode()).hexdigest()}


def _build_report():
    return build_wms_conformance_report(
        compiled_profile=WMS_CONFORMANCE_COMPILED_PROFILE,
        cases=WMS_PROVIDER_CONFORMANCE_CASES,
        observations=_matching_observations(),
        target=ConformanceTarget.REAL_TCP,
        fixture_digest=FIXTURE_DIGEST,
        generated_at=GENERATED_AT,
        **REPORT_METADATA,
    )


def _mismatched_observations() -> tuple[OperationConformanceObservation, ...]:
    observations = list(_matching_observations())
    observations[0] = observations[0].model_copy(update={"semantic_marker": "MISMATCH"})
    return tuple(observations)


def test_manifest_uses_the_reviewed_mode_family_question_banks() -> None:
    manifest = build_wms_conformance_manifest(WMS_CONFORMANCE_COMPILED_PROFILE)

    assert tuple(item.operation for item in manifest.operations) == WMS_OPERATIONS
    assert all(item.required_cases == conformance_cases_for_operation(item.operation) for item in manifest.operations)
    assert sum(len(item.required_cases) for item in manifest.operations) == 193


def test_report_is_bound_to_active_profile_and_deterministically_verifiable() -> None:
    first = _build_report()
    second = _build_report()

    assert first == second
    assert first.profile_identity == WMS_CONFORMANCE_COMPILED_PROFILE.profile.profile.identity
    assert first.suite_version == "wms-provider-full-factory.v2"
    assert first.passed is True
    assert (
        verify_wms_conformance_report(
            first.model_dump(mode="json"),
            compiled_profile=WMS_CONFORMANCE_COMPILED_PROFILE,
        )
        == first
    )


def test_release_builder_rejects_self_consistent_failed_real_tcp_report() -> None:
    with pytest.raises(ValueError, match="all cases to pass"):
        build_wms_release_conformance_report(
            compiled_profile=WMS_CONFORMANCE_COMPILED_PROFILE,
            cases=WMS_PROVIDER_CONFORMANCE_CASES,
            observations=_mismatched_observations(),
            target=ConformanceTarget.REAL_TCP,
            fixture_digest=FIXTURE_DIGEST,
            generated_at=GENERATED_AT,
            **REPORT_METADATA,
        )


def test_release_verifier_rejects_self_consistent_failed_real_tcp_report() -> None:
    report = build_wms_conformance_report(
        compiled_profile=WMS_CONFORMANCE_COMPILED_PROFILE,
        cases=WMS_PROVIDER_CONFORMANCE_CASES,
        observations=_mismatched_observations(),
        target=ConformanceTarget.REAL_TCP,
        fixture_digest=FIXTURE_DIGEST,
        generated_at=GENERATED_AT,
        **REPORT_METADATA,
    )
    payload = report.model_dump(mode="json")

    assert report.passed is False
    assert verify_wms_conformance_report(payload, compiled_profile=WMS_CONFORMANCE_COMPILED_PROFILE) == report
    with pytest.raises(ValueError, match="all cases to pass"):
        verify_wms_release_conformance_report(
            payload,
            compiled_profile=WMS_CONFORMANCE_COMPILED_PROFILE,
        )


def test_report_schema_has_no_signature_trust_or_secret_surface() -> None:
    report = _build_report()
    fields = set(type(report).model_fields)
    serialized = report.model_dump_json().lower()

    assert fields.isdisjoint({"staging_attestation", "signature", "signing_key_id"})
    assert "secret://" not in serialized
    assert "credential" not in serialized
    assert "header" not in serialized


@pytest.mark.parametrize(
    ("field", "value", "error"),
    (
        ("suite_digest", "f" * 64, "suite digest"),
        ("profile_identity", "wms.unconfigured-provider", "profile identity"),
        ("endpoint_digest", "f" * 64, "endpoint digest"),
        ("contract_version", "unexpected", "contract version"),
    ),
)
def test_report_verify_rejects_redigested_binding_drift(field: str, value: str, error: str) -> None:
    payload = _build_report().model_dump(mode="json")
    payload[field] = value

    with pytest.raises(ValueError, match=error):
        verify_wms_conformance_report(
            _redigest_report_payload(payload),
            compiled_profile=WMS_CONFORMANCE_COMPILED_PROFILE,
        )


@pytest.mark.parametrize("mutation", ("reordered", "duplicated", "missing"))
def test_report_verify_rejects_case_identity_order_or_count_drift(mutation: str) -> None:
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
        verify_wms_conformance_report(
            _redigest_report_payload(payload),
            compiled_profile=WMS_CONFORMANCE_COMPILED_PROFILE,
        )


def test_report_verify_rejects_redigested_incorrect_case_verdict() -> None:
    payload = _build_report().model_dump(mode="json")
    cases = list(payload["cases"])
    cases[0] = {**cases[0], "semantic_marker": "WRONG"}
    payload["cases"] = cases

    with pytest.raises(ValueError, match="case result"):
        verify_wms_conformance_report(
            _redigest_report_payload(payload),
            compiled_profile=WMS_CONFORMANCE_COMPILED_PROFILE,
        )


@pytest.mark.parametrize("mutation", ("digest", "safety", "passed", "naive_time"))
def test_report_model_rejects_invalid_integrity_metadata(mutation: str) -> None:
    payload = _build_report().model_dump(mode="json")
    if mutation == "digest":
        payload["report_digest"] = "f" * 64
    elif mutation == "safety":
        payload["execution_safety_confirmed"] = False
        payload = _redigest_report_payload(payload)
    elif mutation == "passed":
        payload["passed"] = False
        payload = _redigest_report_payload(payload)
    else:
        payload["generated_at"] = "2026-07-30T08:00:00"
        payload = _redigest_report_payload(payload)

    with pytest.raises(ValidationError):
        type(_build_report()).model_validate(payload)


def test_report_model_rejects_target_provenance_mismatch() -> None:
    payload = _build_report().model_dump(mode="json")
    payload["provenance"] = "REPLAY"

    with pytest.raises(ValidationError, match="target/provenance"):
        type(_build_report()).model_validate(_redigest_report_payload(payload))


@pytest.mark.parametrize(
    "payload",
    (
        {
            "case_id": "invalid_success",
            "outcome_kind": "SUCCESS",
            "reason_code": "WRONG",
            "retryable": None,
            "evidence_recorded": True,
            "semantic_marker": "SUCCESS",
        },
        {
            "case_id": "invalid_technical",
            "outcome_kind": "TECHNICAL_FAILURE",
            "reason_code": None,
            "retryable": None,
            "evidence_recorded": True,
            "semantic_marker": "TECHNICAL_FAILURE",
        },
        {
            "case_id": "invalid_business",
            "outcome_kind": "BUSINESS_REJECT",
            "reason_code": None,
            "retryable": None,
            "evidence_recorded": True,
            "semantic_marker": "BUSINESS_REJECT",
        },
        {
            "case_id": "invalid_retry_after",
            "outcome_kind": "TECHNICAL_FAILURE",
            "reason_code": "WMS_PROVIDER_TIMEOUT",
            "retryable": False,
            "retry_after_seconds": 1,
            "evidence_recorded": True,
            "semantic_marker": "TECHNICAL_FAILURE",
        },
    ),
)
def test_q14_verdict_rejects_invalid_closed_outcome(payload) -> None:
    from src.app.runtime.system_capabilities.wms.provider_conformance import ConformanceObservation

    with pytest.raises(ValidationError):
        ConformanceObservation.model_validate(payload)


def test_report_verify_rejects_operation_identity_coverage_drift() -> None:
    payload = _build_report().model_dump(mode="json")
    payload["operation_identities"] = list(reversed(payload["operation_identities"]))

    with pytest.raises(ValueError, match="operation identity coverage"):
        verify_wms_conformance_report(
            _redigest_report_payload(payload),
            compiled_profile=WMS_CONFORMANCE_COMPILED_PROFILE,
        )


def test_report_rejects_missing_duplicate_or_unknown_observations() -> None:
    observations = _matching_observations()
    for invalid in (
        observations[:-1],
        (*observations, observations[0]),
        (
            *observations[:-1],
            observations[-1].model_copy(update={"operation_identity": "wms.extra.unknown@v1"}),
        ),
    ):
        with pytest.raises(ValueError, match="exactly once"):
            build_wms_conformance_report(
                compiled_profile=WMS_CONFORMANCE_COMPILED_PROFILE,
                cases=WMS_PROVIDER_CONFORMANCE_CASES,
                observations=invalid,
                target=ConformanceTarget.REAL_TCP,
                fixture_digest=FIXTURE_DIGEST,
                generated_at=GENERATED_AT,
                **REPORT_METADATA,
            )


def test_report_builder_rejects_shared_bank_override() -> None:
    with pytest.raises(ValueError, match="cannot be overridden"):
        build_wms_conformance_report(
            compiled_profile=WMS_CONFORMANCE_COMPILED_PROFILE,
            cases=WMS_PROVIDER_CONFORMANCE_CASES[:-1],
            observations=_matching_observations()[:-1],
            target=ConformanceTarget.REAL_TCP,
            fixture_digest=FIXTURE_DIGEST,
            generated_at=GENERATED_AT,
            **REPORT_METADATA,
        )
