"""WMS NONE inbound authentication startup wiring regressions."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

from src.app.wms_adapter import WmsInboundAuthPolicy
from src.app.wms_integration.provider_profile import WmsProviderAuthScheme
from tests.contracts.wms_integration.provider_profile_support import (
    build_compiled_provider_profile,
    build_provider_profile_payload,
)


@pytest.fixture(autouse=True)
def _device_command_runtime_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ECS_CONNECT_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("ECS_READ_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("DEVICE_COMMAND_QUEUE", "device-command")


def test_none_policy_is_derived_from_the_compiled_profile() -> None:
    compiled_profile = build_compiled_provider_profile()

    policy = WmsInboundAuthPolicy.from_compiled_profile(compiled_profile)

    assert policy.inbound_auth_scheme is WmsProviderAuthScheme.NONE
    assert policy.network_trust_mode == compiled_profile.profile.network_trust_mode
    assert policy.profile_digest == compiled_profile.profile_digest


@pytest.mark.asyncio
async def test_fastapi_startup_binds_and_shutdown_clears_the_compiled_wms_policy() -> None:
    from src import register

    compiled_profile = build_compiled_provider_profile()
    startup = SimpleNamespace(compiled_profile=compiled_profile, catalog=SimpleNamespace())
    transport_runtime = SimpleNamespace(service=object(), repository=object(), client=object(), aclose=AsyncMock())
    build_transport_runtime = AsyncMock(return_value=transport_runtime)
    app = FastAPI()
    with (
        patch(
            "src.app.runtime.system_capabilities.wms.provider_catalog.validate_wms_transport_configuration",
            return_value=startup,
        ),
        patch(
            "src.app.runtime.orchestration.repositories.northbound_operations_repository."
            "northbound_operations_repository.bind_provider_catalog"
        ),
        patch("src.database.db.init_db", new=AsyncMock()),
        patch("src.database.db.close_db", new=AsyncMock()),
        patch("src.database.db.AsyncSessionLocal", new=MagicMock()),
        patch("src.database.redis_client.init_redis", new=AsyncMock()),
        patch("src.database.redis_client.close_redis", new=AsyncMock()),
        patch("src.app.transport.composition.build_transport_runtime", new=build_transport_runtime),
        patch(
            "src.app.wms_integration.query_runtime.build_wms_data_lane_query_runtime",
            return_value=SimpleNamespace(),
        ),
        patch("src.app.wms_integration.query_runtime.bind_wms_data_lane_query_runtime"),
        patch("src.app.wms_integration.query_runtime.close_bound_wms_data_lane_query_runtime", new=AsyncMock()),
        patch(
            "src.app.wms_integration.effect_preparation_runtime.build_wms_effect_preparation_runtime",
            return_value=SimpleNamespace(),
        ),
        patch("src.app.wms_integration.effect_preparation_runtime.bind_wms_effect_preparation_runtime"),
        patch(
            "src.app.wms_integration.effect_preparation_runtime.close_wms_effect_preparation_runtime", new=AsyncMock()
        ),
        patch(
            "src.app.runtime.orchestration.observability.configure_runtime_open_telemetry_backend", return_value=False
        ),
        patch("src.app.runtime.orchestration.observability.runtime_observability_registry.close", MagicMock()),
    ):
        async with register.register_init(app):
            policy = app.state.wms_inbound_auth_policy
            assert policy.profile_digest == compiled_profile.profile_digest
            assert policy.inbound_auth_scheme is WmsProviderAuthScheme.NONE
            assert app.state.transport_runtime is transport_runtime

    assert app.state.wms_inbound_auth_policy is None
    assert app.state.transport_runtime is None
    transport_runtime.aclose.assert_awaited_once()
    build_transport_runtime.assert_awaited_once()


@pytest.mark.asyncio
async def test_transport_profile_rejection_precedes_database_and_transport_resource_creation() -> None:
    from src import register

    payload = build_provider_profile_payload()
    payload["inbound_auth"] = {
        "scheme": "HMAC_SHA256",
        "credential_reference": "secret://wms/inbound@v1",
    }
    startup = SimpleNamespace(
        compiled_profile=build_compiled_provider_profile(payload),
        catalog=SimpleNamespace(),
    )
    init_db = AsyncMock()
    build_transport_runtime = AsyncMock()
    app = FastAPI()

    with (
        patch(
            "src.app.runtime.system_capabilities.wms.provider_catalog.validate_wms_transport_configuration",
            return_value=startup,
        ),
        patch("src.database.db.init_db", new=init_db),
        patch("src.app.transport.composition.build_transport_runtime", new=build_transport_runtime),
    ):
        with pytest.raises(ValueError, match=r"inbound_auth\.scheme=NONE"):
            async with register.register_init(app):
                pytest.fail("unsupported Transport profile must not enter the application lifespan")

    init_db.assert_not_awaited()
    build_transport_runtime.assert_not_awaited()
