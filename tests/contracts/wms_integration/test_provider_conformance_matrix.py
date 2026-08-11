"""T8 全工厂 WMS Provider conformance 通用矩阵。"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime

import pytest

from src.app.runtime.system_capabilities.wms import provider_conformance
from src.app.runtime.system_capabilities.wms.conformance_manifest import build_wms_conformance_manifest
from src.app.wms_integration import provider_manifest
from src.app.wms_integration.operation_contract import WmsCompletionMode, WmsOperationMode
from src.app.wms_integration.operation_registry import WMS_OPERATIONS
from tests.support.wms_provider_conformance import WMS_CONFORMANCE_COMPILED_PROFILE

GENERATED_AT = datetime(2026, 7, 30, 8, 0, tzinfo=UTC)


def test_manifest_is_exact_registry_driven_29_operation_mode_family_matrix() -> None:
    manifest = build_wms_conformance_manifest(WMS_CONFORMANCE_COMPILED_PROFILE)

    assert tuple(item.operation for item in manifest.operations) == WMS_OPERATIONS
    assert len(manifest.operations) == 29
    assert sum(item.operation.mode is WmsOperationMode.QUERY for item in manifest.operations) == 18
    assert sum(item.operation.mode is WmsOperationMode.EFFECT for item in manifest.operations) == 11
    assert sum(item.operation.completion_mode is WmsCompletionMode.SYNC_RESULT for item in manifest.operations) == 9
    assert sum(item.operation.completion_mode is WmsCompletionMode.ASYNC_TASK for item in manifest.operations) == 2
    assert sum(len(item.required_cases) for item in manifest.operations) == 181
    for item in manifest.operations:
        if item.operation.identity == "wms.inventory.query_inventory@v1":
            expected = provider_manifest._INVENTORY_QUERY_CASES
        elif item.operation.mode is WmsOperationMode.QUERY:
            expected = provider_manifest._QUERY_CASES
        elif item.operation.completion_mode is WmsCompletionMode.ASYNC_TASK:
            expected = provider_manifest._ASYNC_EFFECT_CASES
        else:
            expected = provider_manifest._SYNC_EFFECT_CASES
        assert item.required_cases == expected


@pytest.mark.parametrize("mutation", ("duplicate", "missing", "wrong_cases"))
def test_conformance_manifest_rejects_identity_or_mode_family_drift(mutation: str) -> None:
    from src.app.runtime.system_capabilities.wms.conformance_manifest import WmsConformanceManifest

    manifest = build_wms_conformance_manifest(WMS_CONFORMANCE_COMPILED_PROFILE)
    operations = list(manifest.operations)
    if mutation == "duplicate":
        operations[-1] = operations[0]
        error = "duplicate"
    elif mutation == "missing":
        operations.pop()
        error = "exact 29"
    else:
        operations[0] = operations[0].model_copy(update={"required_cases": ("success",)})
        error = "mode family"

    with pytest.raises(ValueError, match=error):
        WmsConformanceManifest(
            profile_identity=manifest.profile_identity,
            fixture_root=manifest.fixture_root,
            operations=tuple(operations),
        )


def test_generic_case_bank_covers_all_181_mode_family_cases_exactly_once() -> None:
    cases = provider_conformance.WMS_PROVIDER_CONFORMANCE_CASES
    expected = {
        (requirement.operation.identity, case_id)
        for requirement in provider_manifest.WMS_CONFORMANCE_REQUIREMENTS
        for case_id in requirement.required_cases
    }

    actual = {(case.operation_identity, case.case_id) for case in cases}
    assert len(cases) == len(expected) == 181
    assert actual == expected


def test_effect_idempotency_and_progress_cases_preserve_frozen_semantics() -> None:
    cases = provider_conformance.WMS_PROVIDER_CONFORMANCE_CASES
    conflicts = tuple(case for case in cases if case.case_id == "idempotency_conflict")
    in_progress = tuple(case for case in cases if case.case_id == "in_progress")
    partial_failures = tuple(case for case in cases if case.case_id == "partial_failure")

    assert len(conflicts) == 11
    assert all(
        case.outcome_kind == "CONTRACT_FAILURE"
        and case.reason_code == "IDEMPOTENCY_CONFLICT"
        and case.retryable is False
        for case in conflicts
    )
    assert len(in_progress) == 9
    assert all(
        case.outcome_kind == "IN_PROGRESS"
        and case.reason_code == "IDEMPOTENCY_REQUEST_IN_PROGRESS"
        and case.retryable is True
        for case in in_progress
    )
    assert len(partial_failures) == 2
    assert all(
        case.outcome_kind == "PARTIAL_FAILURE" and case.semantic_marker == "PARTIAL_FAILURE" and case.retryable is False
        for case in partial_failures
    )


def test_provider_manifest_guards_business_coverage_and_full_registry(monkeypatch) -> None:
    from src.app.wms_integration import operation_registry

    expected = tuple(operation.identity for operation in WMS_OPERATIONS)
    provider_manifest.require_full_factory_registry(expected)
    with pytest.raises(ValueError, match="29-operation"):
        provider_manifest.require_full_factory_registry(expected[:-1])

    monkeypatch.setattr(
        operation_registry,
        "WMS_OPERATION_BY_IDENTITY",
        {**operation_registry.WMS_OPERATION_BY_IDENTITY, "wms.extra.fake@v1": WMS_OPERATIONS[0]},
    )
    with pytest.raises(RuntimeError, match="business scenario"):
        importlib.reload(provider_manifest)
    monkeypatch.undo()
    importlib.reload(provider_manifest)


def test_fixture_matrix_reuses_typed_happy_fixtures_and_is_fail_closed() -> None:
    from tests.mock.wms_operation_fixtures import (
        IDENTITY_MISMATCH_FIXTURES,
        REJECT_FIXTURES,
        REQUEST_FIXTURES,
        RESULT_FIXTURES,
    )
    from tests.support.wms_conformance_runner import build_operation_fixture_matrix

    matrix = build_operation_fixture_matrix(
        operations=WMS_OPERATIONS,
        request_fixtures=tuple(REQUEST_FIXTURES.items()),
        result_fixtures=tuple(RESULT_FIXTURES.items()),
        reject_fixtures=tuple(REJECT_FIXTURES.items()),
        identity_mismatch_fixtures=tuple(IDENTITY_MISMATCH_FIXTURES.items()),
    )

    assert tuple(item.operation.identity for item in matrix) == tuple(
        operation.identity for operation in WMS_OPERATIONS
    )
    for item in matrix:
        assert isinstance(item.request, item.operation.request_model)
        assert isinstance(item.result, item.operation.result_model)
        assert item.reject.operation_identity == item.operation.identity
        assert item.identity_mismatch.actual_operation_identity != item.operation.identity


def test_fixture_matrix_is_built_during_module_import_for_collection_fail_closed() -> None:
    from tests.mock.wms_operation_fixtures import WMS_OPERATION_FIXTURE_MATRIX

    assert len(WMS_OPERATION_FIXTURE_MATRIX) == 29


@pytest.mark.parametrize("mutation", ("missing", "extra", "duplicate"))
def test_fixture_parameter_generation_rejects_missing_extra_or_duplicate_identity(mutation: str) -> None:
    from tests.mock.wms_operation_fixtures import (
        IDENTITY_MISMATCH_FIXTURES,
        REJECT_FIXTURES,
        REQUEST_FIXTURES,
        RESULT_FIXTURES,
    )
    from tests.support.wms_conformance_runner import build_operation_fixture_matrix

    request_pairs = list(REQUEST_FIXTURES.items())
    if mutation == "missing":
        request_pairs.pop()
    elif mutation == "extra":
        request_pairs.append(("wms.extra.not_registered@v1", {}))
    else:
        request_pairs.append(request_pairs[0])

    with pytest.raises(ValueError, match="fixture identities"):
        build_operation_fixture_matrix(
            operations=WMS_OPERATIONS,
            request_fixtures=tuple(request_pairs),
            result_fixtures=tuple(RESULT_FIXTURES.items()),
            reject_fixtures=tuple(REJECT_FIXTURES.items()),
            identity_mismatch_fixtures=tuple(IDENTITY_MISMATCH_FIXTURES.items()),
        )


def test_report_binds_full_operation_coverage_and_real_tcp_provenance() -> None:
    observations = tuple(
        provider_conformance.OperationConformanceObservation.model_validate(case.model_dump(mode="json"))
        for case in provider_conformance.WMS_PROVIDER_CONFORMANCE_CASES
    )
    endpoint_digest = provider_conformance.conformance_endpoint_digest(WMS_CONFORMANCE_COMPILED_PROFILE)

    report = provider_conformance.build_wms_conformance_report(
        compiled_profile=WMS_CONFORMANCE_COMPILED_PROFILE,
        cases=provider_conformance.WMS_PROVIDER_CONFORMANCE_CASES,
        observations=observations,
        target=provider_conformance.ConformanceTarget.REAL_TCP,
        fixture_digest="a" * 64,
        generated_at=GENERATED_AT,
        wms_build_version="wms-build-2026.07.30",
        responsible_person="WMS-OWNER-001",
        execution_safety_confirmed=True,
    )

    assert report.operation_identities == tuple(operation.identity for operation in WMS_OPERATIONS)
    assert report.endpoint_digest == endpoint_digest
    assert report.contract_version == WMS_CONFORMANCE_COMPILED_PROFILE.profile.profile.contract_version
    assert report.wms_build_version == "wms-build-2026.07.30"
    assert report.responsible_person == "WMS-OWNER-001"
    assert report.execution_safety_confirmed is True
    assert report.provenance == "REAL_TCP"
    assert report.passed is True


def test_release_report_rejects_non_real_tcp_provenance_or_missing_safety_confirmation() -> None:
    observations = tuple(
        provider_conformance.OperationConformanceObservation.model_validate(case.model_dump(mode="json"))
        for case in provider_conformance.WMS_PROVIDER_CONFORMANCE_CASES
    )
    common = {
        "compiled_profile": WMS_CONFORMANCE_COMPILED_PROFILE,
        "cases": provider_conformance.WMS_PROVIDER_CONFORMANCE_CASES,
        "observations": observations,
        "fixture_digest": "a" * 64,
        "generated_at": GENERATED_AT,
        "wms_build_version": "wms-build-2026.07.30",
        "responsible_person": "WMS-OWNER-001",
    }

    with pytest.raises(ValueError, match="REAL_TCP"):
        provider_conformance.build_wms_release_conformance_report(
            **common,
            target=provider_conformance.ConformanceTarget.REPLAY,
            execution_safety_confirmed=True,
        )
    with pytest.raises(ValueError, match="safety"):
        provider_conformance.build_wms_release_conformance_report(
            **common,
            target=provider_conformance.ConformanceTarget.REAL_TCP,
            execution_safety_confirmed=False,
        )


def test_local_report_is_allowed_but_release_verifier_requires_real_tcp() -> None:
    observations = tuple(
        provider_conformance.OperationConformanceObservation.model_validate(case.model_dump(mode="json"))
        for case in provider_conformance.WMS_PROVIDER_CONFORMANCE_CASES
    )
    report = provider_conformance.build_wms_conformance_report(
        compiled_profile=WMS_CONFORMANCE_COMPILED_PROFILE,
        cases=provider_conformance.WMS_PROVIDER_CONFORMANCE_CASES,
        observations=observations,
        target=provider_conformance.ConformanceTarget.REPLAY,
        fixture_digest="a" * 64,
        generated_at=GENERATED_AT,
    )

    assert (
        provider_conformance.verify_wms_conformance_report(
            report.model_dump(mode="json"),
            compiled_profile=WMS_CONFORMANCE_COMPILED_PROFILE,
        )
        == report
    )
    with pytest.raises(ValueError, match="REAL_TCP"):
        provider_conformance.verify_wms_release_conformance_report(
            report.model_dump(mode="json"),
            compiled_profile=WMS_CONFORMANCE_COMPILED_PROFILE,
        )


def test_release_verifier_accepts_complete_real_tcp_report() -> None:
    observations = tuple(
        provider_conformance.OperationConformanceObservation.model_validate(case.model_dump(mode="json"))
        for case in provider_conformance.WMS_PROVIDER_CONFORMANCE_CASES
    )
    report = provider_conformance.build_wms_release_conformance_report(
        compiled_profile=WMS_CONFORMANCE_COMPILED_PROFILE,
        cases=provider_conformance.WMS_PROVIDER_CONFORMANCE_CASES,
        observations=observations,
        target=provider_conformance.ConformanceTarget.REAL_TCP,
        fixture_digest="a" * 64,
        generated_at=GENERATED_AT,
        wms_build_version="wms-build-2026.07.30",
        responsible_person="WMS-OWNER-001",
        execution_safety_confirmed=True,
    )

    assert (
        provider_conformance.verify_wms_release_conformance_report(
            report.model_dump(mode="json"),
            compiled_profile=WMS_CONFORMANCE_COMPILED_PROFILE,
        )
        == report
    )


def test_t8_coverage_target_manifest_is_exact_and_forbids_omit_or_pragma() -> None:
    from tests.support.wms_conformance_coverage import T8_COVERAGE_TARGETS, validate_t8_coverage_targets

    assert T8_COVERAGE_TARGETS == (
        "src.app.contracts.external_contract_profile",
        "src.app.contracts.external_contract_profile_catalog",
        "src.app.wms_integration.provider_profile",
        "src.app.wms_integration.provider_manifest",
        "src.app.runtime.system_capabilities.wms.conformance_manifest",
        "src.app.runtime.system_capabilities.wms.provider_conformance",
        "src.app.runtime.system_capabilities.wms.conformance_matrix",
        "tests.support.wms_conformance_runner",
        "scripts.run_wms_conformance",
    )
    validate_t8_coverage_targets()
