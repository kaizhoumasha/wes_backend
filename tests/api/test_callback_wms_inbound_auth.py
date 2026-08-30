"""WMS NONE inbound authentication startup wiring regressions."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

from src.app.wms_adapter import WmsInboundAuthPolicy


@pytest.fixture(autouse=True)
def _device_command_runtime_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ECS_CONNECT_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("ECS_READ_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("DEVICE_COMMAND_QUEUE", "device-command")


def test_none_policy_is_fixed_to_isolated_lan_and_typed_event_admission() -> None:
    policy = WmsInboundAuthPolicy()

    assert policy.allows_unsigned_wms_callbacks is True
    assert policy.permits_unsigned_event({"source_system": "WMS", "event_type": "WMS_GRN_RECEIVED"}) is True
    assert policy.permits_unsigned_event({"source_system": "WMS", "event_type": "unknown@v1"}) is False
    assert policy.permits_unsigned_event({"source_system": "ECS", "event_type": "WMS_GRN_RECEIVED"}) is False


@pytest.mark.asyncio
async def test_fastapi_startup_binds_and_shutdown_clears_the_fixed_wms_policy() -> None:
    from src import register

    transport_runtime = SimpleNamespace(
        service=object(),
        repository=object(),
        client=object(),
        position_projection_service=object(),
        aclose=AsyncMock(),
    )
    build_transport_runtime = AsyncMock(return_value=transport_runtime)
    session_factory = MagicMock()
    app = FastAPI()
    with (
        patch("src.database.db.init_db", new=AsyncMock()),
        patch("src.database.db.close_db", new=AsyncMock()),
        patch("src.database.db.AsyncSessionLocal", new=session_factory),
        patch("src.database.redis_client.init_redis", new=AsyncMock()),
        patch("src.database.redis_client.close_redis", new=AsyncMock()),
        patch("src.app.transport.composition.build_transport_runtime", new=build_transport_runtime),
    ):
        async with register.register_init(app):
            policy = app.state.wms_inbound_auth_policy
            assert policy == WmsInboundAuthPolicy()
            assert app.state.transport_runtime is transport_runtime

    assert app.state.wms_inbound_auth_policy is None
    assert app.state.transport_runtime is None
    transport_runtime.aclose.assert_awaited_once()
    build_transport_runtime.assert_awaited_once_with(
        wms_base_url=register.settings.WMS_BASE_URL,
        transport_submit_path=register.settings.TRANSPORT_SUBMIT_PATH,
        session_factory=session_factory,
    )
