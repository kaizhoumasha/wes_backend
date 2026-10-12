"""统一只读日志 API、摘要/详情 DTO 与权限的 FAST owner。"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.app.sys.models.api_access_log import APIAccessLogSummary
from src.app.sys.v1 import api_access_log as module
from src.database.db import get_db


def _row():
    return {
        "id": 1,
        "system_id": "ecs",
        "direction": "INBOUND",
        "method": "POST",
        "path": "/event",
        "created_at": datetime(2026, 10, 9),
        "details": {"attempt": 1},
    }


def test_only_readonly_query_and_detail_routes_with_canonical_permissions():
    routes = {(route.path, next(iter(route.methods))): route for route in module.router.routes}
    assert set(routes) == {("/api-access-logs/query", "POST"), ("/api-access-logs/{id}", "GET")}
    for key, permission in (
        (("/api-access-logs/query", "POST"), "sys:apiaccesslog:list"),
        (("/api-access-logs/{id}", "GET"), "sys:apiaccesslog:detail"),
    ):
        assert [
            dep.call.permission_required
            for dep in routes[key].dependant.dependencies
            if hasattr(dep.call, "permission_required")
        ] == [permission]


def test_query_delegates_to_service_and_excludes_details(monkeypatch):
    service = SimpleNamespace(query_summary=AsyncMock(return_value=(1, [APIAccessLogSummary.model_validate(_row())])))
    monkeypatch.setattr(module, "api_access_log_service", service)
    route = next(route for route in module.router.routes if route.path.endswith("/query"))
    import asyncio

    from src.core.query_models import QueryOptions

    response = asyncio.run(route.endpoint(db=object(), options=QueryOptions(limit=20)))
    assert response["data"]["total"] == 1
    assert response["data"]["offset"] == 0
    assert response["data"]["limit"] == 20
    assert "details" not in response["data"]["items"][0].model_dump()
    service.query_summary.assert_awaited_once()


def test_detail_delegates_to_service_and_returns_snapshot_or_not_found(monkeypatch):
    service = SimpleNamespace(get_by_id=AsyncMock(return_value=_row()))
    monkeypatch.setattr(module, "api_access_log_service", service)
    route = next(route for route in module.router.routes if route.path.endswith("/{id}"))
    import asyncio

    response = asyncio.run(route.endpoint(id=1, db=object()))
    assert response["data"].details == {"attempt": 1}
    assert response["data"].created_at.utcoffset().total_seconds() == 0
    service.get_by_id.assert_awaited_once()
    service.get_by_id.return_value = None
    response = asyncio.run(route.endpoint(id=1, db=object()))
    assert response["code"] != "1000"


def test_http_query_validates_options_and_serializes_summary_with_utc(monkeypatch):
    service = SimpleNamespace(query_summary=AsyncMock(return_value=(1, [APIAccessLogSummary.model_validate(_row())])))
    monkeypatch.setattr(module, "api_access_log_service", service)
    app = FastAPI()
    app.include_router(module.router, prefix="/api/v1/sys")
    app.dependency_overrides[get_db] = lambda: object()
    for route in module.router.routes:
        for dependency in route.dependencies:
            app.dependency_overrides[dependency.dependency] = lambda: None
    with TestClient(app) as client:
        response = client.post("/api/v1/sys/api-access-logs/query", json={"limit": 1})
        assert response.status_code == 200
        assert response.json()["data"]["items"][0]["status_code"] is None
        assert response.json()["data"]["items"][0]["created_at"].endswith("Z")
        assert "details" not in response.json()["data"]["items"][0]
        assert client.post("/api/v1/sys/api-access-logs/query", json={"limit": 0}).status_code == 422
        assert client.post("/api/v1/sys/api-access-logs/1", json={}).status_code == 405
        assert client.delete("/api/v1/sys/api-access-logs/1").status_code == 405
    service.query_summary.assert_awaited_once()


def test_host_registers_only_unified_routes_and_permission_catalog():
    from src.register import create_app
    from src.utils.permission_scanner import build_permission_catalog

    app = create_app()
    assert {(route.path, frozenset(route.methods)) for route in app.routes if "api-access-logs" in route.path} == {
        ("/api/v1/sys/api-access-logs/query", frozenset({"POST"})),
        ("/api/v1/sys/api-access-logs/{id}", frozenset({"GET"})),
    }
    catalog = build_permission_catalog(app)
    assert {
        row["name"]: (row["method"], row["path"])
        for row in catalog
        if "apiaccesslog" in row["name"] and not row["name"].endswith(":group")
    } == {
        "sys:apiaccesslog:list": ("POST", "/api/v1/sys/api-access-logs/query"),
        "sys:apiaccesslog:detail": ("GET", "/api/v1/sys/api-access-logs/{id}"),
    }
    assert all(not row["name"].startswith(("callback:callback_log:", "api-auth:apiaccesslog:")) for row in catalog)
