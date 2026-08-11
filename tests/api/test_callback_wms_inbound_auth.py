"""WMS NONE 入站认证接线的 ASGI 回归测试。"""

from __future__ import annotations

import hashlib
import json
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.app.callback.models import (
    build_callback_event_accepted_response,
    build_callback_external_accepted_response,
)
from src.app.callback.services.callback_ingress_service import CallbackEventIngressDecision
from src.app.callback.v1 import callback as callback_module
from src.app.wms_adapter import WmsInboundAuthPolicy
from src.app.wms_integration.provider_profile import WmsProviderAuthScheme
from src.core.api_security import calculate_body_hmac_signature
from src.core.conf import settings
from src.core.error_handlers import register_exception_handlers
from src.core.response import response_builder
from src.database.db import get_db
from src.database.dependencies import _get_cache_service
from src.database.redis_cache import get_cache
from src.utils.permission_scanner import scan_routes_for_permissions
from tests.contracts.wms_integration.provider_profile_support import (
    build_compiled_provider_profile,
    build_hmac_provider_profile_payload,
    build_provider_profile_payload,
)

if TYPE_CHECKING:
    from collections.abc import Generator


def _wms_event(event_type: object = "WMS_GRN_RECEIVED", *, source_system: object = "WMS") -> dict[str, object]:
    return {
        "source_system": source_system,
        "event_type": event_type,
        "source_event_id": "event-wms-inbound-auth",
        "source_version": "1",
        "occurred_at": "2026-07-30T08:00:00Z",
        "request_id": "wms-request-001",
        "correlation_id": "corr-wms-001",
        "data": {},
    }


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


def _valid_wms_grn_event(**overrides: object) -> dict[str, object]:
    payload = _wms_event()
    payload["data"] = {
        "grn_id": "GRN-001",
        "po_number": "PO-001",
        "po_item": "10",
        "material_code": "MAT-001",
        "received_quantity": 5,
        "warehouse_code": "WH-A",
    }
    payload.update(overrides)
    return payload


def _event_response(event_type: str) -> dict[str, object]:
    return response_builder.success(
        data=build_callback_event_accepted_response(
            status="submitted",
            device_code=None,
            source_system="WMS",
            event_type=event_type,
            request_id="request-1",
            trace_id="trace-1",
            event_id="event-1",
            causation_id=None,
        )
    )


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
    event_handler = AsyncMock(return_value=CallbackEventIngressDecision(body=_event_response("WMS_GRN_RECEIVED")))
    external_handler = AsyncMock(return_value=_external_response())
    with (
        patch.object(callback_module.callback_ingress_service, "handle_event_decision", event_handler),
        patch.object(callback_module.callback_ingress_service, "handle_external", external_handler),
        TestClient(app) as client,
    ):
        yield client


def _bind_policy(client: TestClient, compiled_profile: object) -> WmsInboundAuthPolicy:
    policy = WmsInboundAuthPolicy.from_compiled_profile(compiled_profile)  # type: ignore[arg-type]
    client.app.state.wms_inbound_auth_policy = policy
    return policy


@pytest.mark.parametrize(
    "event_type",
    (
        "WMS_GRN_RECEIVED",
        "WMS_PALLET_ARRIVED",
        "WMS_INVENTORY_UPDATED",
        "WMS_PDA_OPERATION_RECORDED",
    ),
)
def test_isolated_lan_none_profile_admits_exact_wms_events_without_api_auth(
    callback_client: TestClient,
    event_type: str,
) -> None:
    _bind_policy(callback_client, build_compiled_provider_profile())

    response = callback_client.post("/api/v1/callback/event", json=_wms_event(event_type))

    assert response.status_code == 200


def test_isolated_lan_none_profile_admits_exact_wms_status_hint_without_api_auth(
    callback_client: TestClient,
) -> None:
    _bind_policy(callback_client, build_compiled_provider_profile())

    response = callback_client.post("/api/v1/callback/external", json=_wms_status_hint())

    assert response.status_code == 200


@pytest.mark.parametrize(
    ("path", "payload"),
    (
        ("/api/v1/callback/event", _wms_event("WMS_UNKNOWN")),
        ("/api/v1/callback/event", _wms_event("WMS_GRN_RECEIVED", source_system="ECS")),
        ("/api/v1/callback/event", _wms_event("DEVICE_ONLINE", source_system="DEVICE")),
        ("/api/v1/callback/event", _wms_event("AGV_TASK_RESULT", source_system="AGV")),
        ("/api/v1/callback/external", _wms_status_hint("WMS_OTHER")),
        ("/api/v1/callback/external", _wms_status_hint(source_system="AGV")),
        ("/api/v1/callback/event", _wms_event([])),
        ("/api/v1/callback/event", _wms_event({"event": "WMS_GRN_RECEIVED"})),
        ("/api/v1/callback/event", _wms_event(None)),
        ("/api/v1/callback/external", _wms_status_hint([])),
        ("/api/v1/callback/external", _wms_status_hint({"type": "WMS_EFFECT_STATUS_HINT"})),
    ),
)
def test_non_exact_payloads_fall_back_to_api_auth(
    callback_client: TestClient,
    path: str,
    payload: dict[str, object],
) -> None:
    _bind_policy(callback_client, build_compiled_provider_profile())

    response = callback_client.post(path, json=payload)

    assert response.status_code == 401


def test_missing_policy_fails_closed_for_unsigned_wms_callback(callback_client: TestClient) -> None:
    response = callback_client.post("/api/v1/callback/event", json=_wms_event())

    assert response.status_code == 401


def test_unsigned_wms_callback_still_uses_the_bounded_body_reader(
    callback_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.core.conf import settings

    _bind_policy(callback_client, build_compiled_provider_profile())
    monkeypatch.setattr(settings, "callback_request_body_max_bytes", 32)

    response = callback_client.post("/api/v1/callback/event", json=_wms_event())

    assert response.status_code == 413


def test_none_policy_is_derived_from_the_compiled_profile() -> None:
    compiled_profile = build_compiled_provider_profile()

    policy = WmsInboundAuthPolicy.from_compiled_profile(compiled_profile)

    assert policy.inbound_auth_scheme is WmsProviderAuthScheme.NONE
    assert policy.network_trust_mode == compiled_profile.profile.network_trust_mode
    assert policy.profile_digest == compiled_profile.profile_digest


def test_wms_inbound_auth_policy_has_no_callback_services_compatibility_export() -> None:
    from src.app import wms_adapter
    from src.app.callback import services

    assert getattr(wms_adapter, "WmsInboundAuthPolicy", None) is WmsInboundAuthPolicy
    assert not hasattr(services, "WmsInboundAuthPolicy")


def test_callback_routes_keep_api_permission_scanner_metadata() -> None:
    app = FastAPI()
    app.include_router(callback_module.router, prefix="/api/v1/callback")

    scanned = {item["name"]: item for item in scan_routes_for_permissions(app)}

    assert scanned["api:callback:event"]["type"] == "app_api"
    assert scanned["api:callback:event"]["path"] == "/api/v1/callback/event"


def test_two_apps_keep_their_frozen_wms_auth_policies_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "SKIP_API_AUTH", False)
    apps = [FastAPI(), FastAPI()]
    for app in apps:
        app.include_router(callback_module.router, prefix="/api/v1/callback")
        register_exception_handlers(app)

        async def _db_override():
            yield SimpleNamespace()

        app.dependency_overrides[get_db] = _db_override
        app.dependency_overrides[get_cache] = lambda: SimpleNamespace()

    hmac_policy = WmsInboundAuthPolicy.from_compiled_profile(
        build_compiled_provider_profile(build_hmac_provider_profile_payload())
    )
    none_policy = WmsInboundAuthPolicy.from_compiled_profile(build_compiled_provider_profile())
    apps[0].state.wms_inbound_auth_policy = hmac_policy
    apps[1].state.wms_inbound_auth_policy = none_policy
    with (
        patch.object(
            callback_module.callback_ingress_service,
            "handle_event_decision",
            AsyncMock(return_value=CallbackEventIngressDecision(body=_event_response("WMS_GRN_RECEIVED"))),
        ),
        TestClient(apps[0]) as hmac_client,
        TestClient(apps[1]) as none_client,
    ):
        assert hmac_client.post("/api/v1/callback/event", json=_wms_event()).status_code == 401
        assert none_client.post("/api/v1/callback/event", json=_wms_event()).status_code == 200
        none_client.app.state.wms_inbound_auth_policy = None
        assert hmac_client.post("/api/v1/callback/event", json=_wms_event()).status_code == 401


@pytest.mark.asyncio
async def test_fastapi_startup_binds_and_shutdown_clears_the_compiled_wms_policy() -> None:
    from src import register

    compiled_profile = build_compiled_provider_profile()
    startup = SimpleNamespace(compiled_profile=compiled_profile, catalog=SimpleNamespace())
    transport_runtime = SimpleNamespace(aclose=AsyncMock())
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


@pytest.mark.asyncio
async def test_startup_validation_begins_fail_closed_before_profile_compilation() -> None:
    from src import register

    app = FastAPI()
    app.state.wms_inbound_auth_policy = WmsInboundAuthPolicy.from_compiled_profile(build_compiled_provider_profile())

    def _startup_failure(*, settings_source: object) -> None:
        assert getattr(app.state, "wms_inbound_auth_policy", None) is None
        raise ValueError("invalid provider profile")

    with patch(
        "src.app.runtime.system_capabilities.wms.provider_catalog.validate_wms_transport_configuration",
        side_effect=_startup_failure,
    ):
        with pytest.raises(ValueError, match="invalid provider profile"):
            async with register.register_init(app):
                pytest.fail("startup must not yield after profile validation failure")


def test_real_hmac_pre_read_reuses_cached_body_for_ingress_h4_and_nonce_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "SKIP_API_AUTH", False)
    app = FastAPI()
    app.include_router(callback_module.router, prefix="/api/v1/callback")
    register_exception_handlers(app)
    app.state.wms_inbound_auth_policy = WmsInboundAuthPolicy.from_compiled_profile(
        build_compiled_provider_profile(build_hmac_provider_profile_payload())
    )
    cache = SimpleNamespace(
        set_if_absent=AsyncMock(side_effect=[True, False, True]),
        incr_with_expire=AsyncMock(return_value=1),
    )

    async def _db_override():
        yield SimpleNamespace()

    app.dependency_overrides[get_db] = _db_override
    app.dependency_overrides[_get_cache_service] = lambda: cache
    api_app = SimpleNamespace(
        id=1,
        app_id="wms-hmac",
        app_name="WMS",
        app_type="external",
        app_secret_encrypted="encrypted",
        status="active",
        expires_at=None,
        ip_whitelist=[],
        rate_limit_per_minute=100,
        rate_limit_per_hour=5000,
    )

    def _signed_headers(body: bytes, nonce: str) -> dict[str, str]:
        timestamp = str(int(time.time()))
        body_hash = hashlib.sha256(body).hexdigest()
        return {
            "Content-Type": "application/json",
            "X-App-ID": "wms-hmac",
            "X-Timestamp": timestamp,
            "X-Nonce": nonce,
            "X-Body-SHA256": body_hash,
            "X-Signature": calculate_body_hmac_signature(
                app_secret="wms-secret",
                method="POST",
                path="/api/v1/callback/event",
                timestamp=timestamp,
                nonce=nonce,
                body_sha256=body_hash,
                app_id="wms-hmac",
            ),
        }

    duplicate_outcome = SimpleNamespace(trace_id="trace-wms-001", is_duplicate=True)
    with (
        patch(
            "src.app.api_auth.services.api_app_service.get_by_app_id",
            new=AsyncMock(return_value=api_app),
        ),
        patch("src.app.api_auth.services.get_app_permissions", new=AsyncMock(return_value={"api:callback:event"})),
        patch("src.core.api_security.encryption_service.decrypt", return_value="wms-secret"),
        patch(
            "src.app.callback.services.callback_ingress_service.callback_orchestration_service.process_wms_event",
            new=AsyncMock(return_value=duplicate_outcome),
        ) as process_wms_event,
        patch(
            "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
            new=AsyncMock(),
        ),
        patch(
            "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
            new=AsyncMock(),
        ),
        TestClient(app) as client,
    ):
        valid_body = json.dumps(_valid_wms_grn_event(), separators=(",", ":")).encode()
        valid_headers = _signed_headers(valid_body, "nonce-valid")
        accepted = client.post("/api/v1/callback/event", content=valid_body, headers=valid_headers)
        replay = client.post("/api/v1/callback/event", content=valid_body, headers=valid_headers)

        h4_body = json.dumps(_valid_wms_grn_event(plc_address="10.0.0.8"), separators=(",", ":")).encode()
        h4 = client.post("/api/v1/callback/event", content=h4_body, headers=_signed_headers(h4_body, "nonce-h4"))

    assert accepted.status_code == 200
    assert accepted.json()["data"]["status"] == "duplicate"
    assert replay.status_code == 401
    assert h4.status_code == 400
    process_wms_event.assert_awaited_once()
    assert cache.set_if_absent.await_count == 3
