"""诊断 API 只读合同与 Service 边界。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.app.wms_diagnostics.contracts import ExchangePage
from src.app.wms_diagnostics.v1.exchanges import router


def app_and_service():
    app = FastAPI()
    service = SimpleNamespace(list_exchanges=AsyncMock(), get_exchange=AsyncMock())
    app.state.wms_diagnostics_service = service
    app.include_router(router, prefix="/api")
    for route in router.routes:
        for dependency in route.dependencies:
            action = (
                "stream"
                if route.path.endswith("/stream")
                else "read"
                if route.path.endswith("/{exchange_id}")
                else "query"
            )
            assert dependency.dependency.permission_required == f"ops:wms-diagnostics:{action}"
            app.dependency_overrides[dependency.dependency] = lambda: None
    return app, service


def test_list_uses_typed_query_and_returns_incomplete_scan_cursor() -> None:
    app, service = app_and_service()
    service.list_exchanges.return_value = ExchangePage(
        items=[], next_cursor="1-0", scan_incomplete=True, retention_hours=24
    )
    with TestClient(app) as client:
        result = client.get("/api/v1/wms-diagnostics/exchanges?operation=sample@v1")
    assert result.status_code == 200
    assert result.json()["data"]["scan_incomplete"] is True
    assert service.list_exchanges.call_args.args[0].operation == "sample@v1"


@pytest.mark.parametrize("failure,status", [(None, 404), (ConnectionError("offline"), 503)])
def test_detail_distinguishes_expired_from_unavailable(failure, status) -> None:
    app, service = app_and_service()
    service.get_exchange.return_value = None
    service.get_exchange.side_effect = failure
    with TestClient(app) as client:
        response = client.get("/api/v1/wms-diagnostics/exchanges/1-0")
    assert response.status_code == status


def test_invalid_query_cannot_reach_service_and_stream_precedes_dynamic_route() -> None:
    app, service = app_and_service()
    with TestClient(app) as client:
        response = client.get("/api/v1/wms-diagnostics/exchanges?page_size=101")
    assert response.status_code == 422
    service.list_exchanges.assert_not_awaited()
    assert [route.path for route in router.routes].index("/v1/wms-diagnostics/exchanges/stream") < [
        route.path for route in router.routes
    ].index("/v1/wms-diagnostics/exchanges/{exchange_id}")


def test_application_registers_all_three_diagnostic_routes() -> None:
    from src.register import register_routers

    app = FastAPI()
    register_routers(app)
    paths = app.openapi()["paths"]
    assert "/api/v1/wms-diagnostics/exchanges" in paths
    assert "/api/v1/wms-diagnostics/exchanges/stream" in paths
    assert "/api/v1/wms-diagnostics/exchanges/{exchange_id}" in paths


@pytest.mark.parametrize(
    "path,permission,allowed_status",
    [
        ("", "query", 200),
        ("/1-0", "read", 404),
        ("/stream", "stream", 200),
    ],
)
@pytest.mark.parametrize("granted", [False, True])
def test_ordinary_engineer_requires_each_route_permission(monkeypatch, path, permission, allowed_status, granted):
    from src.core import rbac
    from src.core.error_handlers import register_exception_handlers
    from src.database import dependencies

    app, service = app_and_service()
    app.dependency_overrides.clear()
    register_exception_handlers(app)
    app.dependency_overrides[rbac.require_auth] = lambda: 123
    app.dependency_overrides[dependencies.get_db] = lambda: None
    app.dependency_overrides[dependencies._get_cache_service] = lambda: None
    monkeypatch.setattr(
        rbac,
        "get_user_permissions",
        AsyncMock(return_value={f"ops:wms-diagnostics:{permission}"} if granted else set()),
    )
    service.list_exchanges.return_value = ExchangePage(
        items=[], next_cursor=None, scan_incomplete=False, retention_hours=24
    )
    service.get_exchange.return_value = None

    async def stream(_query):
        yield ": heartbeat\n\n"

    service.stream_events = stream
    with TestClient(app) as client:
        response = client.get(f"/api/v1/wms-diagnostics/exchanges{path}")
    assert response.status_code == (allowed_status if granted else 403)


def test_live_stream_rejects_history_only_parameters():
    app, service = app_and_service()
    service.stream_events = MagicMock(side_effect=AssertionError("invalid filter must not subscribe"))
    with TestClient(app) as client:
        response = client.get("/api/v1/wms-diagnostics/exchanges/stream?cursor=1-0")
    assert response.status_code == 422
