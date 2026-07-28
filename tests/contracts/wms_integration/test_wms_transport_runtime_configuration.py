"""typed WMS transport 运行配置门禁。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import httpx
import pytest

from src.app.runtime.system_capabilities.wms import provider_catalog
from src.app.sys.canonical_dispatch import canonical_json_bytes, payload_sha256
from src.app.sys.external_http_credentials import build_environment_external_http_credential_provider
from src.app.wms_integration.ports.effect_status import FrozenWmsEffectStatusBinding
from src.app.wms_integration.ports.query_inventory_operation import InventoryQueryOperationRequest
from src.app.wms_integration.ports.query_outcome import QueryContractFailure
from src.app.wms_integration.runtime_factory import build_inventory_query_port_factory
from src.app.wms_integration.services.query_transport import WmsBoundQueryEndpoint, WmsQueryCallPermit
from src.core.conf import Settings

if TYPE_CHECKING:
    from collections.abc import Mapping


class _EvidenceWriter:
    async def before_call(self, *, operation_identity: str, target_code: str) -> WmsQueryCallPermit:
        return WmsQueryCallPermit(allowed=True)

    async def record(
        self,
        *,
        operation_identity: str,
        target_code: str,
        request_snapshot: Mapping[str, object],
        outcome: object,
        permit: WmsQueryCallPermit,
    ) -> str:
        return "evidence-1"


def _status_settings(**overrides: object) -> Settings:
    return Settings(  # pyright: ignore[reportCallIssue]
        _env_file=".env.dev",
        DATABASE_RUNTIME_ROLE="cli",
        DATABASE_POOL_SIZE=1,
        **overrides,
    )


def test_wms_effect_status_runtime_configuration_exposes_all_frozen_budgets() -> None:
    configured = _status_settings()

    assert configured.WMS_EFFECT_STATUS_URL
    assert configured.WMS_EFFECT_STATUS_TIMEOUT_SECONDS > 0
    assert configured.WMS_EFFECT_STATUS_MAX_RESPONSE_BYTES > 0
    assert configured.WMS_EFFECT_IDEMPOTENCY_RETENTION_SECONDS > 0
    assert configured.WMS_EFFECT_STATUS_VISIBILITY_SLA_SECONDS > 0
    assert configured.WES_EFFECT_MAX_CONFIRMATION_AGE_SECONDS > 0
    assert configured.WES_EFFECT_NOT_FOUND_GRACE_SECONDS > 0
    assert configured.WES_EFFECT_STATUS_SAFETY_MARGIN_SECONDS > 0
    assert configured.WES_EFFECT_STATUS_SCAN_BATCH_SIZE > 0
    assert configured.WES_EFFECT_STATUS_CLAIM_LEASE_SECONDS >= configured.WMS_EFFECT_STATUS_TIMEOUT_SECONDS
    assert configured.WES_EFFECT_STATUS_MAX_QUERY_ATTEMPTS > 0
    assert 0 < configured.WES_EFFECT_STATUS_INITIAL_BACKOFF_SECONDS <= configured.WES_EFFECT_STATUS_MAX_BACKOFF_SECONDS


def test_material_flow_credential_rotation_keeps_provider_identity_and_resolves_frozen_revisions() -> None:
    old_settings = SimpleNamespace(APP_ENV="test", WMS_MATERIAL_FLOW_ACTIVE_HMAC_VERSION="v1")
    active_settings = SimpleNamespace(
        APP_ENV="test",
        WMS_MATERIAL_FLOW_ACTIVE_HMAC_VERSION="v2",
        WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V1="old-secret",
        WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V2="new-secret",
        WES_REVOKED_EXTERNAL_HTTP_CREDENTIAL_REFERENCES="",
    )

    old_profile = provider_catalog.build_active_wms_provider_profile(old_settings)
    active_profile = provider_catalog.build_active_wms_provider_profile(active_settings)
    old_reference = old_profile.bindings[0].outbound_auth.credential_reference
    active_reference = active_profile.bindings[0].outbound_auth.credential_reference
    credential_provider = build_environment_external_http_credential_provider(settings_source=active_settings)
    old_profile_hash = payload_sha256(
        canonical_json_bytes(
            {
                "credential_reference": old_reference,
                "provider_profile_identity": old_profile.identity.identity,
            }
        )
    )
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
        "provider_profile_identity": old_profile.identity.identity,
        "target": target,
        "target_hash": target_hash,
    }
    frozen_old_binding = FrozenWmsEffectStatusBinding.from_persisted(
        snapshot=old_binding_snapshot,
        snapshot_hash=payload_sha256(canonical_json_bytes(old_binding_snapshot)),
    )

    assert old_profile.identity.identity == active_profile.identity.identity
    assert old_reference == "secret://wms/material-flow-sandbox-hmac@v1"
    assert active_reference == "secret://wms/material-flow-sandbox-hmac@v2"
    assert credential_provider.resolve(frozen_old_binding.credential_reference) == b"old-secret"
    assert credential_provider.resolve(active_reference) == b"new-secret"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"WMS_EFFECT_STATUS_URL": ""}, "WMS_EFFECT_STATUS_URL"),
        ({"WMS_EFFECT_STATUS_TIMEOUT_SECONDS": 0}, "WMS_EFFECT_STATUS_TIMEOUT_SECONDS"),
        ({"WES_EFFECT_STATUS_CLAIM_LEASE_SECONDS": 1, "WMS_EFFECT_STATUS_TIMEOUT_SECONDS": 2}, "lease"),
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


def _configure_transport(
    monkeypatch: pytest.MonkeyPatch,
    *,
    base_url: str,
    app_env: str = "prod",
    sandbox_secret: str = "",
    production_secret: str = "",
    revoked_references: str = "",
) -> None:
    monkeypatch.setattr(provider_catalog.settings, "APP_ENV", app_env)
    monkeypatch.setattr(provider_catalog.settings, "WMS_QUERY_IN_PROCESS_SIMULATION_ENABLED", False)
    monkeypatch.setattr(provider_catalog.settings, "WMS_MATERIAL_FLOW_ACTIVE_HMAC_VERSION", "v1")
    monkeypatch.setattr(
        provider_catalog.settings,
        "WMS_EFFECT_STATUS_URL",
        "https://wms.example/api/status" if app_env == "prod" else "http://localhost:8011/api/wms/status",
    )
    monkeypatch.setattr(provider_catalog.settings, "WMS_SYNC_BASE_URL", base_url)
    monkeypatch.setattr(provider_catalog.settings, "WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V1", sandbox_secret)
    monkeypatch.setattr(provider_catalog.settings, "WMS_MATERIAL_FLOW_PRODUCTION_HMAC_SECRET_V1", production_secret)
    monkeypatch.setattr(
        provider_catalog.settings,
        "WES_REVOKED_EXTERNAL_HTTP_CREDENTIAL_REFERENCES",
        revoked_references,
    )


def test_wms_sync_base_url_has_no_implicit_http_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WMS_SYNC_BASE_URL", raising=False)
    monkeypatch.setattr(provider_catalog, "settings", SimpleNamespace(WMS_SYNC_BASE_URL=""), raising=False)

    with pytest.raises(ValueError, match="WMS_SYNC_BASE_URL"):
        provider_catalog.wms_sync_base_url()


def test_wms_transport_reads_url_and_hmac_from_pydantic_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WMS_SYNC_BASE_URL", raising=False)
    monkeypatch.delenv("WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V1", raising=False)
    monkeypatch.setattr(
        provider_catalog,
        "settings",
        SimpleNamespace(
            APP_ENV="dev",
            WMS_QUERY_IN_PROCESS_SIMULATION_ENABLED=False,
            WMS_MATERIAL_FLOW_ACTIVE_HMAC_VERSION="v1",
            WMS_SYNC_BASE_URL="http://localhost:8011/api/wms",
            WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V1="settings-material-flow-secret",
            WES_REVOKED_EXTERNAL_HTTP_CREDENTIAL_REFERENCES="",
            WMS_EFFECT_STATUS_URL="http://localhost:8011/api/wms/status",
            WMS_EFFECT_IDEMPOTENCY_RETENTION_SECONDS=100,
            WMS_EFFECT_STATUS_VISIBILITY_SLA_SECONDS=2,
            WES_EFFECT_MAX_CONFIRMATION_AGE_SECONDS=80,
            WES_EFFECT_NOT_FOUND_GRACE_SECONDS=3,
            WES_EFFECT_STATUS_SAFETY_MARGIN_SECONDS=20,
        ),
        raising=False,
    )

    provider_catalog.validate_wms_transport_configuration(settings_source=provider_catalog.settings)


def test_production_wms_transport_requires_explicit_https_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_transport(
        monkeypatch,
        base_url="http://wms.example/api",
        production_secret="production-secret",
    )

    with pytest.raises(ValueError, match="HTTPS"):
        provider_catalog.validate_wms_transport_configuration(settings_source=provider_catalog.settings)


@pytest.mark.parametrize("missing_field", ("WMS_MATERIAL_FLOW_PRODUCTION_HMAC_SECRET_V1",))
def test_wms_transport_requires_every_active_hmac_secret(
    monkeypatch: pytest.MonkeyPatch,
    missing_field: str,
) -> None:
    _configure_transport(
        monkeypatch,
        base_url="https://wms.example/api",
        production_secret="production-secret",
    )
    monkeypatch.setattr(provider_catalog.settings, missing_field, "")

    with pytest.raises(ValueError, match=missing_field):
        provider_catalog.validate_wms_transport_configuration(settings_source=provider_catalog.settings)


def test_valid_active_profile_configuration_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_transport(
        monkeypatch,
        base_url="https://wms.example/api",
        production_secret="production-secret",
    )

    provider_catalog.validate_wms_transport_configuration(settings_source=provider_catalog.settings)


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
def test_wms_transport_rejects_non_origin_base_urls(
    monkeypatch: pytest.MonkeyPatch,
    base_url: str,
) -> None:
    _configure_transport(
        monkeypatch,
        base_url=base_url,
        production_secret="production-secret",
    )

    with pytest.raises(ValueError, match=r"absolute|userinfo|query|fragment"):
        provider_catalog.validate_wms_transport_configuration(settings_source=provider_catalog.settings)


def test_query_endpoint_rejects_missing_hostname() -> None:
    binding = provider_catalog.resolve_wms_operation_binding(
        profile_identity=provider_catalog.WMS_PROVIDER_PROFILE.identity.identity,
        operation_identity="wms.inventory.query_inventory@v1",
    )

    with pytest.raises(ValueError, match="absolute"):
        WmsBoundQueryEndpoint(binding=binding, base_url="https:///api")


def test_production_runtime_rejects_explicit_in_process_simulation() -> None:
    with pytest.raises(ValueError, match=r"production.*forbids.*simulation"):
        build_inventory_query_port_factory(
            simulation=True,
            sandbox_rows_provider=lambda **_kwargs: [],
            settings_source=SimpleNamespace(
                APP_ENV="prod",
                WMS_MATERIAL_FLOW_ACTIVE_HMAC_VERSION="v2",
            ),
        )


@pytest.mark.asyncio
async def test_query_factory_honors_shared_credential_revocation_before_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference = "secret://wms/material-flow-sandbox-hmac@v2"
    _configure_transport(
        monkeypatch,
        base_url="https://wms.example/api",
        app_env="test",
        sandbox_secret="sandbox-secret",
        revoked_references=reference,
    )
    monkeypatch.setattr(provider_catalog.settings, "WMS_MATERIAL_FLOW_ACTIVE_HMAC_VERSION", "v2")
    monkeypatch.setattr(
        provider_catalog.settings,
        "WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V2",
        "sandbox-secret-v2",
    )
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"items": []})

    factory = build_inventory_query_port_factory(
        simulation=False,
        sandbox_rows_provider=lambda **_kwargs: [],
        transport=httpx.MockTransport(handler),
        evidence_writer=_EvidenceWriter(),
        base_url="https://wms.example/api",
        settings_source=provider_catalog.settings,
    )

    outcome = await factory().execute(InventoryQueryOperationRequest(material_code="MAT-001"))

    assert isinstance(outcome, QueryContractFailure)
    assert outcome.reason_code == "WMS_CREDENTIAL_UNAVAILABLE"
    assert calls == 0
