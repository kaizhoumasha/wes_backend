"""typed WMS transport 运行配置门禁。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.app.runtime.system_capabilities.wms import provider_catalog
from src.app.sys.canonical_dispatch import canonical_json_bytes, payload_sha256
from src.app.sys.external_http_credentials import build_environment_external_http_credential_provider
from src.app.wms_integration.ports.effect_status import FrozenWmsEffectStatusBinding
from src.core.conf import Settings
from tests.contracts.wms_integration.provider_profile_support import (
    build_compiled_provider_profile,
    build_hmac_provider_profile_payload,
    build_provider_catalog,
    build_provider_profile_payload,
    write_provider_profile,
)


def _status_settings(**overrides: object) -> Settings:
    return Settings(  # pyright: ignore[reportCallIssue]
        _env_file=".env.dev",
        DATABASE_RUNTIME_ROLE="cli",
        DATABASE_POOL_SIZE=1,
        **overrides,
    )


def test_wms_effect_status_runtime_configuration_exposes_all_frozen_budgets() -> None:
    configured = _status_settings()

    assert configured.WMS_EFFECT_STATUS_TIMEOUT_SECONDS > 0
    assert configured.WMS_EFFECT_STATUS_MAX_RESPONSE_BYTES > 0
    assert configured.WMS_EFFECT_IDEMPOTENCY_RETENTION_SECONDS > 0
    assert configured.WMS_EFFECT_STATUS_VISIBILITY_SLA_SECONDS > 0
    assert configured.WES_EFFECT_MAX_CONFIRMATION_AGE_SECONDS > 0
    assert configured.WES_EFFECT_NOT_FOUND_GRACE_SECONDS > 0
    assert configured.WES_EFFECT_STATUS_SAFETY_MARGIN_SECONDS > 0
    assert configured.WES_EFFECT_STATUS_SCAN_BATCH_SIZE > 0
    assert 0 < configured.WES_EFFECT_STATUS_MAX_IN_FLIGHT <= configured.DATABASE_POOL_SIZE
    assert configured.WES_EFFECT_STATUS_SCAN_PERIOD_SECONDS > 0
    assert configured.WES_EFFECT_STATUS_CLAIM_LEASE_SECONDS > configured.WES_EFFECT_STATUS_SCAN_BATCH_BUDGET_SECONDS
    assert (
        configured.WES_EFFECT_STATUS_TASK_HARD_TIME_LIMIT_SECONDS
        > configured.WES_EFFECT_STATUS_TASK_SOFT_TIME_LIMIT_SECONDS
        > configured.WES_EFFECT_STATUS_SCAN_BATCH_BUDGET_SECONDS
    )
    assert configured.WES_EFFECT_STATUS_MAX_QUERY_ATTEMPTS > 0
    assert 0 < configured.WES_EFFECT_STATUS_INITIAL_BACKOFF_SECONDS <= configured.WES_EFFECT_STATUS_MAX_BACKOFF_SECONDS


def test_material_flow_credential_rotation_keeps_provider_identity_and_resolves_frozen_revisions() -> None:
    active_settings = SimpleNamespace(
        APP_ENV="test",
        WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V1="old-secret",
        WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V2="new-secret",
        WES_REVOKED_EXTERNAL_HTTP_CREDENTIAL_REFERENCES="",
    )

    old_payload = build_hmac_provider_profile_payload()
    active_payload = build_hmac_provider_profile_payload()
    old_payload["outbound_auth"]["credential_reference"] = "secret://wms/material-flow-sandbox-hmac@v1"
    active_payload["outbound_auth"]["credential_reference"] = "secret://wms/material-flow-sandbox-hmac@v2"
    old_profile = build_compiled_provider_profile(old_payload)
    active_profile = build_compiled_provider_profile(active_payload)
    old_reference = old_profile.profile.outbound_auth.credential_reference
    active_reference = active_profile.profile.outbound_auth.credential_reference
    credential_provider = build_environment_external_http_credential_provider(settings_source=active_settings)
    old_profile_hash = old_profile.profile_digest
    target = {
        "url": "https://old-wms.example.test/status",
        "http_method": "GET",
        "timeout_seconds": 2.0,
        "max_response_bytes": 4096,
    }
    target_hash = payload_sha256(canonical_json_bytes(target))
    old_binding_snapshot = {
        "auth_scheme": "HMAC_SHA256",
        "binding_revision": payload_sha256(
            canonical_json_bytes(
                {
                    "auth_scheme": "HMAC_SHA256",
                    "credential_reference": old_reference,
                    "provider_profile_hash": old_profile_hash,
                    "target_hash": target_hash,
                }
            )
        ),
        "credential_reference": old_reference,
        "provider_profile_hash": old_profile_hash,
        "provider_profile_identity": old_profile.profile.profile.identity,
        "target": target,
        "target_hash": target_hash,
    }
    frozen_old_binding = FrozenWmsEffectStatusBinding.from_persisted(
        snapshot=old_binding_snapshot,
        snapshot_hash=payload_sha256(canonical_json_bytes(old_binding_snapshot)),
    )

    assert old_profile.profile.profile.identity == active_profile.profile.profile.identity
    assert old_profile.profile_digest != active_profile.profile_digest
    assert old_reference == "secret://wms/material-flow-sandbox-hmac@v1"
    assert active_reference == "secret://wms/material-flow-sandbox-hmac@v2"
    assert credential_provider.resolve(frozen_old_binding.credential_reference) == b"old-secret"
    assert credential_provider.resolve(active_reference) == b"new-secret"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"WMS_EFFECT_STATUS_TIMEOUT_SECONDS": 0}, "WMS_EFFECT_STATUS_TIMEOUT_SECONDS"),
        ({"WES_EFFECT_STATUS_MAX_IN_FLIGHT": 2}, "session"),
        ({"WES_EFFECT_STATUS_SCAN_BATCH_SIZE": 50}, "QPS"),
        ({"WES_EFFECT_STATUS_CLAIM_LEASE_SECONDS": 15}, "lease"),
        (
            {
                "WES_EFFECT_STATUS_INITIAL_BACKOFF_SECONDS": 9,
                "WES_EFFECT_STATUS_MAX_BACKOFF_SECONDS": 8,
            },
            "backoff",
        ),
        (
            {
                "WMS_EFFECT_IDEMPOTENCY_RETENTION_SECONDS": 8,
                "WES_EFFECT_MAX_CONFIRMATION_AGE_SECONDS": 6,
                "WES_EFFECT_STATUS_SAFETY_MARGIN_SECONDS": 3,
            },
            "retention",
        ),
        (
            {
                "WMS_EFFECT_STATUS_VISIBILITY_SLA_SECONDS": 4,
                "WES_EFFECT_NOT_FOUND_GRACE_SECONDS": 3,
            },
            "visibility",
        ),
    ],
)
def test_wms_effect_status_runtime_configuration_fails_fast(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _status_settings(**overrides)


def _profile_startup_settings(profile_file) -> SimpleNamespace:
    return SimpleNamespace(
        APP_ENV="prod",
        WMS_QUERY_IN_PROCESS_SIMULATION_ENABLED=False,
        WMS_PROVIDER_PROFILE_FILE=profile_file,
        WMS_EFFECT_IDEMPOTENCY_RETENTION_SECONDS=100,
        WMS_EFFECT_STATUS_VISIBILITY_SLA_SECONDS=2,
        WES_EFFECT_MAX_CONFIRMATION_AGE_SECONDS=80,
        WES_EFFECT_NOT_FOUND_GRACE_SECONDS=3,
        WES_EFFECT_STATUS_SAFETY_MARGIN_SECONDS=20,
    )


def test_wms_transport_reads_the_deployment_owned_profile_file(tmp_path) -> None:
    profile_file = write_provider_profile(tmp_path / "provider.yaml")

    startup = provider_catalog.validate_wms_transport_configuration(
        settings_source=_profile_startup_settings(profile_file)
    )

    assert startup.compiled_profile.profile.server_url == "http://factory-wms.example:8080"


def test_production_wms_transport_allows_http_with_isolated_lan_none_auth(tmp_path) -> None:
    profile_file = write_provider_profile(tmp_path / "provider.yaml")

    startup = provider_catalog.validate_wms_transport_configuration(
        settings_source=_profile_startup_settings(profile_file)
    )

    assert startup.compiled_profile.profile.outbound_auth.scheme == "NONE"


def test_hmac_profile_requires_only_a_versioned_reference_not_an_embedded_secret(tmp_path) -> None:
    payload = build_provider_profile_payload()
    payload["outbound_auth"] = {
        "scheme": "HMAC_SHA256",
        "credential_reference": "secret://wms/factory-hmac@v1",
    }
    profile_file = write_provider_profile(tmp_path / "provider.yaml", payload)

    startup = provider_catalog.validate_wms_transport_configuration(
        settings_source=_profile_startup_settings(profile_file)
    )

    assert startup.compiled_profile.profile.outbound_auth.credential_reference == "secret://wms/factory-hmac@v1"


@pytest.mark.parametrize(
    "base_url",
    (
        "https:///api",
        "https://user:password@wms.example/api",
        "https://wms.example/api?tenant=one",
        "https://wms.example/api#fragment",
        "https://wms.example:notaport/api",
        "https://wms.example:70000/api",
        "https://wms.example:/api",
        "https://wms .example/api",
        "https://wms.\texample/api",
        "https://wms.\nexample/api",
        "https://[::1/api",
        "https://wms.example/api?",
        "https://wms.example/api#",
    ),
)
def test_wms_transport_rejects_non_origin_base_urls(tmp_path, base_url: str) -> None:
    payload = build_provider_profile_payload()
    payload["server_url"] = base_url
    profile_file = write_provider_profile(tmp_path / "provider.yaml", payload)

    with pytest.raises(ValueError, match=r"HTTP\(S\) origin"):
        provider_catalog.validate_wms_transport_configuration(settings_source=_profile_startup_settings(profile_file))
