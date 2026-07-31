"""compiled Provider profile 是 catalog/conformance/readiness 的唯一 active 真源。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.app.runtime.system_capabilities.wms import provider_catalog
from src.app.runtime.system_capabilities.wms.provider_conformance import (
    WMS_PROVIDER_CONFORMANCE_CASES,
    ConformanceTarget,
    OperationConformanceObservation,
    build_wms_conformance_report,
    verify_wms_conformance_report,
)
from src.app.wms_integration.endpoint_compiler import compile_wms_provider_profile
from src.app.wms_integration.provider_profile import WmsProviderProfileSettings
from tests.contracts.wms_integration.provider_profile_support import build_provider_profile_payload


def _compiled_profile():
    return compile_wms_provider_profile(WmsProviderProfileSettings.model_validate(build_provider_profile_payload()))


def test_catalog_is_a_projection_of_one_explicit_compiled_profile_without_legacy_default() -> None:
    compiled_profile = _compiled_profile()

    catalog = provider_catalog.build_wms_provider_catalog(compiled_profile)
    binding = provider_catalog.resolve_wms_operation_binding(
        catalog=catalog,
        profile_identity=compiled_profile.profile.profile.identity,
        operation_identity="wms.master_data.get_material@v1",
    )

    assert catalog.compiled_profile is compiled_profile
    assert catalog.profile_identity == compiled_profile.profile.profile.identity
    assert catalog.profile_digest == compiled_profile.profile_digest
    assert binding.profile.identity == catalog.profile_identity
    assert binding.outbound_auth.scheme.value == compiled_profile.profile.outbound_auth.scheme.value
    assert binding.outbound_auth.credential_reference is None
    assert not hasattr(provider_catalog, "WMS_PROVIDER_PROFILE")
    assert not hasattr(provider_catalog, "WMS_EXTERNAL_HTTP_EFFECT_PROFILE")
    assert not hasattr(provider_catalog, "build_active_wms_provider_profile")


def test_wms_runtime_profile_identity_has_no_application_environment_dimension() -> None:
    from src.app.runtime.system_capabilities.wms.scheduling_identity import wms_runtime_profile_identity

    assert wms_runtime_profile_identity() == "wms.2026-07-28.full-factory"


def test_catalog_external_contract_identity_rejects_environment_field() -> None:
    from pydantic import ValidationError

    from src.app.runtime.system_capabilities.wms.contracts import ExternalContractProfile

    profile = ExternalContractProfile(
        provider_code="WMS",
        contract_version="2026-07-28.full-factory",
    )
    assert profile.identity == "wms.2026-07-28.full-factory"

    with pytest.raises(ValidationError, match="environment"):
        ExternalContractProfile.model_validate(
            {
                "provider_code": "WMS",
                "contract_version": "2026-07-28.full-factory",
                "environment": "production",
            }
        )


def test_conformance_report_and_manifest_bind_the_explicit_compiled_profile() -> None:
    from src.app.runtime.system_capabilities.wms import conformance_manifest

    compiled_profile = _compiled_profile()
    observations = tuple(
        OperationConformanceObservation.model_validate(case.model_dump(mode="python"))
        for case in WMS_PROVIDER_CONFORMANCE_CASES
    )

    manifest = conformance_manifest.build_wms_conformance_manifest(compiled_profile)
    report = build_wms_conformance_report(
        compiled_profile=compiled_profile,
        cases=WMS_PROVIDER_CONFORMANCE_CASES,
        observations=observations,
        target=ConformanceTarget.REAL_TCP,
        fixture_digest="a" * 64,
        generated_at=datetime(2026, 7, 29, tzinfo=UTC),
        wms_build_version="wms-build-2026.07.30",
        responsible_person="WMS-OWNER-001",
        execution_safety_confirmed=True,
    )

    assert manifest.profile_identity == compiled_profile.profile.profile.identity
    assert report.profile_identity == compiled_profile.profile.profile.identity
    assert report.profile_digest == compiled_profile.profile_digest
    assert (
        verify_wms_conformance_report(
            report.model_dump(mode="json"),
            compiled_profile=compiled_profile,
        )
        == report
    )

    changed_payload = build_provider_profile_payload()
    changed_payload["server_url"] = "https://rotated-wms.example"
    changed_profile = compile_wms_provider_profile(WmsProviderProfileSettings.model_validate(changed_payload))
    with pytest.raises(ValueError, match="profile identity or digest"):
        verify_wms_conformance_report(
            report.model_dump(mode="json"),
            compiled_profile=changed_profile,
        )
