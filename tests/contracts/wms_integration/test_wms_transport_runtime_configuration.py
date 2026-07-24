"""typed WMS transport 运行配置门禁。"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from src.app.contracts.external_contract_profile_catalog import WMS_MATERIAL_FLOW_PRODUCTION_PROFILE
from src.app.runtime.system_capabilities.wms import provider_catalog
from src.app.wms_integration.ports.query_inventory_operation import InventoryQueryOperationRequest
from src.app.wms_integration.ports.query_outcome import QueryContractFailure
from src.app.wms_integration.runtime_factory import build_inventory_query_port_factory
from src.app.wms_integration.services.query_transport import WmsBoundQueryEndpoint, WmsQueryCallPermit

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


def test_wms_sync_base_url_has_no_implicit_http_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WMS_SYNC_BASE_URL", raising=False)

    with pytest.raises(ValueError, match="WMS_SYNC_BASE_URL"):
        provider_catalog.wms_sync_base_url()


def test_production_wms_transport_requires_explicit_https_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WMS_SYNC_BASE_URL", "http://wms.example/api")
    monkeypatch.setenv("WMS_MATERIAL_FLOW_PRODUCTION_HMAC_SECRET_V1", "production-secret")

    with pytest.raises(ValueError, match="HTTPS"):
        provider_catalog.validate_wms_transport_configuration(app_env="prod")


def test_wms_transport_requires_active_profile_hmac_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WMS_SYNC_BASE_URL", "http://mock_wms:8011/api/wms")
    monkeypatch.delenv("WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V1", raising=False)

    with pytest.raises(ValueError, match="WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V1"):
        provider_catalog.validate_wms_transport_configuration(app_env="dev")


def test_valid_active_profile_configuration_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WMS_SYNC_BASE_URL", "https://wms.example/api")
    monkeypatch.setenv("WMS_MATERIAL_FLOW_PRODUCTION_HMAC_SECRET_V1", "production-secret")

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
    monkeypatch.setenv("WMS_SYNC_BASE_URL", base_url)
    monkeypatch.setenv("WMS_MATERIAL_FLOW_PRODUCTION_HMAC_SECRET_V1", "production-secret")

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
    monkeypatch.setenv("WMS_MATERIAL_FLOW_PRODUCTION_HMAC_SECRET_V1", "production-secret")
    monkeypatch.setenv("WES_REVOKED_EXTERNAL_HTTP_CREDENTIAL_REFERENCES", reference)
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
