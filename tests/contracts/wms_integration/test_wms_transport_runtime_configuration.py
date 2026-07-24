"""typed WMS transport 运行配置门禁。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import httpx
import pytest

from src.app.contracts.external_contract_profile_catalog import WMS_MATERIAL_FLOW_PRODUCTION_PROFILE
from src.app.runtime.system_capabilities.wms import provider_catalog
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
    sandbox_secret: str = "",
    production_secret: str = "",
    legacy_sandbox_secret: str | None = None,
    legacy_production_secret: str | None = None,
    plugin_sandbox_secret: str | None = None,
    plugin_production_secret: str | None = None,
    revoked_references: str = "",
    rack_endpoint_url: str = "https://wms.example/api/rack-operation",
    bin_endpoint_url: str = "https://wms.example/api/transport-request",
    full_box_endpoint_url: str = "https://wms.example/api/full-box-exchange",
) -> None:
    monkeypatch.setattr(provider_catalog.settings, "WMS_SYNC_BASE_URL", base_url)
    monkeypatch.setattr(provider_catalog.settings, "WMS_RCS_RACK_OPERATION_URL", rack_endpoint_url, raising=False)
    monkeypatch.setattr(provider_catalog.settings, "WMS_RCS_BIN_OPERATION_URL", bin_endpoint_url, raising=False)
    monkeypatch.setattr(
        provider_catalog.settings,
        "WMS_RCS_FULL_BOX_EXCHANGE_URL",
        full_box_endpoint_url,
        raising=False,
    )
    monkeypatch.setattr(provider_catalog.settings, "WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V1", sandbox_secret)
    monkeypatch.setattr(provider_catalog.settings, "WMS_MATERIAL_FLOW_PRODUCTION_HMAC_SECRET_V1", production_secret)
    monkeypatch.setattr(
        provider_catalog.settings,
        "WMS_LEGACY_TRANSPORT_SANDBOX_HMAC_SECRET_V1",
        sandbox_secret if legacy_sandbox_secret is None else legacy_sandbox_secret,
    )
    monkeypatch.setattr(
        provider_catalog.settings,
        "WMS_LEGACY_TRANSPORT_PRODUCTION_HMAC_SECRET_V1",
        production_secret if legacy_production_secret is None else legacy_production_secret,
    )
    monkeypatch.setattr(
        provider_catalog.settings,
        "WORKLINE_PLUGIN_RUNTIME_SANDBOX_HMAC_SECRET_V1",
        sandbox_secret if plugin_sandbox_secret is None else plugin_sandbox_secret,
    )
    monkeypatch.setattr(
        provider_catalog.settings,
        "WORKLINE_PLUGIN_RUNTIME_PRODUCTION_HMAC_SECRET_V1",
        production_secret if plugin_production_secret is None else plugin_production_secret,
    )
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
            WMS_SYNC_BASE_URL="http://localhost:8011/api/wms",
            WMS_RCS_RACK_OPERATION_URL="http://localhost:8011/api/wms/rack-operation",
            WMS_RCS_BIN_OPERATION_URL="http://localhost:8011/api/wms/transport-request",
            WMS_RCS_FULL_BOX_EXCHANGE_URL="http://localhost:8011/api/wms/fulfillment/full-box-exchange",
            WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V1="settings-material-flow-secret",
            WMS_LEGACY_TRANSPORT_SANDBOX_HMAC_SECRET_V1="settings-legacy-secret",
            WORKLINE_PLUGIN_RUNTIME_SANDBOX_HMAC_SECRET_V1="settings-plugin-secret",
            WES_REVOKED_EXTERNAL_HTTP_CREDENTIAL_REFERENCES="",
        ),
        raising=False,
    )

    provider_catalog.validate_wms_transport_configuration(app_env="dev")


def test_production_wms_transport_requires_explicit_https_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_transport(
        monkeypatch,
        base_url="http://wms.example/api",
        production_secret="production-secret",
    )

    with pytest.raises(ValueError, match="HTTPS"):
        provider_catalog.validate_wms_transport_configuration(app_env="prod")


@pytest.mark.parametrize(
    "missing_field",
    (
        "WMS_MATERIAL_FLOW_PRODUCTION_HMAC_SECRET_V1",
        "WMS_LEGACY_TRANSPORT_PRODUCTION_HMAC_SECRET_V1",
        "WORKLINE_PLUGIN_RUNTIME_PRODUCTION_HMAC_SECRET_V1",
    ),
)
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
        provider_catalog.validate_wms_transport_configuration(app_env="prod")


def test_valid_active_profile_configuration_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_transport(
        monkeypatch,
        base_url="https://wms.example/api",
        production_secret="production-secret",
    )

    provider_catalog.validate_wms_transport_configuration(app_env="prod")


@pytest.mark.parametrize(
    "endpoint_field",
    (
        "WMS_RCS_RACK_OPERATION_URL",
        "WMS_RCS_BIN_OPERATION_URL",
        "WMS_RCS_FULL_BOX_EXCHANGE_URL",
    ),
)
def test_production_wms_transport_requires_https_for_every_external_http_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    endpoint_field: str,
) -> None:
    _configure_transport(
        monkeypatch,
        base_url="https://wms.example/api",
        production_secret="production-secret",
    )
    monkeypatch.setattr(provider_catalog.settings, endpoint_field, "http://wms.example/insecure", raising=False)

    with pytest.raises(ValueError, match=endpoint_field):
        provider_catalog.validate_wms_transport_configuration(app_env="prod")


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
        provider_catalog.validate_wms_transport_configuration(app_env="prod")


def test_query_endpoint_rejects_missing_hostname() -> None:
    binding = provider_catalog.resolve_wms_operation_binding(
        profile_identity="wms.2026-07-06.material-flow.production",
        operation_identity="wms.inventory.query_inventory@v1",
    )

    with pytest.raises(ValueError, match="absolute"):
        WmsBoundQueryEndpoint(binding=binding, base_url="https:///api")


@pytest.mark.asyncio
async def test_query_factory_honors_shared_credential_revocation_before_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference = "secret://wms/material-flow-production-hmac@v1"
    _configure_transport(
        monkeypatch,
        base_url="https://wms.example/api",
        production_secret="production-secret",
        revoked_references=reference,
    )
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"items": []})

    factory = build_inventory_query_port_factory(
        provider_profile=WMS_MATERIAL_FLOW_PRODUCTION_PROFILE,
        simulation=False,
        sandbox_rows_provider=lambda **_kwargs: [],
        transport=httpx.MockTransport(handler),
        evidence_writer=_EvidenceWriter(),
        base_url="https://wms.example/api",
    )

    outcome = await factory().execute(InventoryQueryOperationRequest(material_code="MAT-001"))

    assert isinstance(outcome, QueryContractFailure)
    assert outcome.reason_code == "WMS_CREDENTIAL_UNAVAILABLE"
    assert calls == 0
