"""WMS NONE 非设备 callback 入站认证接线回归。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.app.callback.models import build_callback_external_accepted_response
from src.app.callback.v1 import callback as callback_module
from src.app.wms_adapter import WmsInboundAuthPolicy
from src.app.wms_integration.provider_profile import WmsProviderAuthScheme
from src.core.conf import settings
from src.core.error_handlers import register_exception_handlers
from src.core.response import response_builder
from src.database.db import get_db
from src.database.redis_cache import get_cache
from src.utils.permission_scanner import scan_routes_for_permissions
from tests.contracts.wms_integration.provider_profile_support import (
    build_compiled_provider_profile,
    build_provider_profile_payload,
)

if TYPE_CHECKING:
    from collections.abc import Generator


@pytest.fixture(autouse=True)
def _device_command_runtime_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ECS_BASE_URL", "http://ecs")
    monkeypatch.setenv("ECS_CONNECT_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("ECS_READ_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("DEVICE_COMMAND_QUEUE", "device-command")


def _wms_status_hint(
    callback_type: object = "WMS_EFFECT_STATUS_HINT", *, source_system: object = "WMS"
) -> dict[str, object]:
    return {
        "source_system": source_system,
        "callback_type": callback_type,
        "source_event_id": "wms-status-hint-001",
        "source_version": "1",
        "occurred_at": "2026-07-30T08:00:00Z",
        "request_id": "wms-status-request-001",
        "trace_id": "trace-wms-status-001",
        "data": {},
    }


def _external_response() -> dict[str, object]:
    return response_builder.success(
        data=build_callback_external_accepted_response(
            status="submitted",
            callback_type="WMS_EFFECT_STATUS_HINT",
            request_id="request-1",
            trace_id="trace-1",
            event_id="event-1",
            causation_id=None,
        )
    )


@pytest.fixture
def callback_client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient]:
    monkeypatch.setattr(settings, "SKIP_API_AUTH", False)
    app = FastAPI()
    app.include_router(callback_module.router, prefix="/api/v1/callback")
    register_exception_handlers(app)

    async def _db_override():
        yield SimpleNamespace()

    app.dependency_overrides[get_db] = _db_override
    app.dependency_overrides[get_cache] = lambda: SimpleNamespace()
    with (
        patch.object(
            callback_module.callback_ingress_service, "handle_external", AsyncMock(return_value=_external_response())
        ),
        TestClient(app) as client,
    ):
        yield client


def _bind_policy(client: TestClient, compiled_profile: object) -> WmsInboundAuthPolicy:
    policy = WmsInboundAuthPolicy.from_compiled_profile(compiled_profile)  # type: ignore[arg-type]
    client.app.state.wms_inbound_auth_policy = policy
    return policy


def test_isolated_lan_none_profile_admits_exact_wms_status_hint_without_api_auth(
    callback_client: TestClient,
) -> None:
    _bind_policy(callback_client, build_compiled_provider_profile())

    response = callback_client.post("/api/v1/callback/external", json=_wms_status_hint())

    assert response.status_code == 200


@pytest.mark.parametrize(
    "payload",
    (
        _wms_status_hint("WMS_OTHER"),
        _wms_status_hint(source_system="AGV"),
        _wms_status_hint([]),
        _wms_status_hint({"type": "WMS_EFFECT_STATUS_HINT"}),
    ),
)
def test_non_exact_payloads_fall_back_to_api_auth(
    callback_client: TestClient,
    payload: dict[str, object],
) -> None:
    _bind_policy(callback_client, build_compiled_provider_profile())

    response = callback_client.post("/api/v1/callback/external", json=payload)

    assert response.status_code == 401


def test_missing_policy_fails_closed_for_unsigned_wms_callback(callback_client: TestClient) -> None:
    response = callback_client.post("/api/v1/callback/external", json=_wms_status_hint())

    assert response.status_code == 401


def test_none_policy_is_derived_from_the_compiled_profile() -> None:
    compiled_profile = build_compiled_provider_profile()

    policy = WmsInboundAuthPolicy.from_compiled_profile(compiled_profile)

    assert policy.inbound_auth_scheme is WmsProviderAuthScheme.NONE
    assert policy.network_trust_mode == compiled_profile.profile.network_trust_mode
    assert policy.profile_digest == compiled_profile.profile_digest


def test_callback_route_keeps_api_permission_scanner_metadata() -> None:
    app = FastAPI()
    app.include_router(callback_module.router, prefix="/api/v1/callback")

    scanned = {item["name"]: item for item in scan_routes_for_permissions(app)}

    assert scanned["api:callback:event"]["type"] == "app_api"
    assert scanned["api:callback:event"]["path"] == "/api/v1/callback/external"


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
