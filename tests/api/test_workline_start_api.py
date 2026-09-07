"""WorkLine START API 的事务与响应合同。"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from src.app.workline.models.workline import LineType, WorkLine
from src.app.workline.services.workline_start_service import (
    WorkLineStartConfigurationError,
    WorkLineStartInvalidStateError,
    WorkLineStartNotFoundError,
    WorkLineStartVersionConflictError,
)
from src.app.workline.v1 import operation as operation_api
from src.core import rbac
from src.core.error_handlers import register_exception_handlers
from src.core.security import require_auth
from src.database.db import get_db
from src.database.dependencies import _get_cache_service


class Db:
    def __init__(self, *, commit_error: Exception | None = None) -> None:
        self.commit_error = commit_error
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1
        if self.commit_error is not None:
            raise self.commit_error

    async def rollback(self) -> None:
        self.rollbacks += 1


class StartService:
    def __init__(self, result: WorkLine | Exception) -> None:
        self.result = result
        self.calls: list[tuple[int, int]] = []

    async def start(self, _db: object, *, workline_id: int, version: int) -> WorkLine:
        self.calls.append((workline_id, version))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _line() -> WorkLine:
    return WorkLine(
        id=7,
        line_code="WL-7",
        line_name="line",
        line_type=LineType.AUTO,
        version=4,
        is_active=True,
        plugin_key="example_plugin",
        plugin_version="1.0",
        flow_mode="GENERIC_FLOW",
    )


def _request(service: object | None) -> object:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(workline_start_service=service)))


def test_start_route_replaces_sandbox_contract_and_uses_dedicated_permission() -> None:
    routes = {getattr(route, "path", None): route for route in operation_api.router.routes}

    assert "/sandbox/worklines/{workline_id}/start" not in routes
    route = routes["/worklines/{workline_id}/start"]
    permissions = [
        getattr(getattr(dependency, "dependency", None), "permission_required", None)
        for dependency in route.dependencies
    ]
    assert permissions == ["biz:workline:start"]


def test_start_openapi_declares_success_and_runtime_failure_contracts_separately() -> None:
    app = FastAPI()
    app.include_router(operation_api.router, prefix="/api/v1/workline/operations")

    responses = app.openapi()["paths"]["/api/v1/workline/operations/worklines/{workline_id}/start"]["post"]["responses"]

    assert set(responses) == {"200", "404", "409", "422", "503"}
    assert responses["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ResponseSchemaModel_WorkLineStartResponse_"
    }
    for status_code in ("404", "409", "503"):
        assert responses[status_code]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ResponseSchemaModel_WorkLineStartErrorResponse_"
        }


def test_start_request_is_closed_and_uses_version() -> None:
    assert operation_api.WorkLineStartRequest(version=3).version == 3
    for invalid in (
        {"request_id": "REQUEST-1"},
        {"version": 3, "request_id": "REQUEST-1"},
        {"version": True},
        {"version": 0},
    ):
        with pytest.raises(ValueError):
            operation_api.WorkLineStartRequest(**invalid)


@pytest.mark.asyncio
async def test_start_commits_once_and_returns_current_workline() -> None:
    service = StartService(_line())
    db = Db()

    body = await operation_api.start_workline(
        workline_id=7,
        payload=operation_api.WorkLineStartRequest(version=3),
        request=_request(service),  # type: ignore[arg-type]
        response=Response(),
        db=db,  # type: ignore[arg-type]
        cache=object(),  # type: ignore[arg-type]
    )

    assert db.commits == 1
    assert service.calls == [(7, 3)]
    assert body["code"] == "1000"
    assert body["data"] == {
        "workline_id": 7,
        "version": 4,
        "plugin_key": "example_plugin",
        "plugin_version": "1.0",
        "flow_mode": "GENERIC_FLOW",
        "is_active": True,
    }


@pytest.mark.asyncio
async def test_start_commit_failure_rolls_back() -> None:
    service = StartService(_line())
    db = Db(commit_error=RuntimeError("commit failed"))

    with pytest.raises(RuntimeError, match="commit failed"):
        await operation_api.start_workline(
            workline_id=7,
            payload=operation_api.WorkLineStartRequest(version=3),
            request=_request(service),  # type: ignore[arg-type]
            response=Response(),
            db=db,  # type: ignore[arg-type]
            cache=object(),  # type: ignore[arg-type]
        )

    assert db.commits == 1
    assert db.rollbacks >= 1


@pytest.mark.asyncio
async def test_start_service_missing_returns_503_without_commit() -> None:
    db = Db()
    http_response = Response()

    body = await operation_api.start_workline(
        workline_id=7,
        payload=operation_api.WorkLineStartRequest(version=3),
        request=_request(None),  # type: ignore[arg-type]
        response=http_response,
        db=db,  # type: ignore[arg-type]
        cache=object(),  # type: ignore[arg-type]
    )

    assert http_response.status_code == 503
    assert body["code"] == "5030"
    assert db.commits == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code", "expected_reason"),
    [
        (WorkLineStartNotFoundError("missing"), 404, "3000", "WORKLINE_NOT_FOUND"),
        (WorkLineStartInvalidStateError("not stopped"), 409, "3012", "INVALID_STATE"),
        (WorkLineStartConfigurationError("bad config"), 409, "3012", "CONFIGURATION_INVALID"),
        (WorkLineStartVersionConflictError("owned elsewhere"), 409, "3012", "VERSION_CONFLICT"),
    ],
)
async def test_start_maps_domain_failures_to_stable_http_reason(
    error: Exception,
    expected_status: int,
    expected_code: str,
    expected_reason: str,
) -> None:
    db = Db()
    http_response = Response()

    body = await operation_api.start_workline(
        workline_id=7,
        payload=operation_api.WorkLineStartRequest(version=3),
        request=_request(StartService(error)),  # type: ignore[arg-type]
        response=http_response,
        db=db,  # type: ignore[arg-type]
        cache=object(),  # type: ignore[arg-type]
    )

    assert http_response.status_code == expected_status
    assert body["code"] == expected_code
    assert body["data"] == {"reason": expected_reason}
    assert db.commits == 0


def _asgi_app(
    monkeypatch: pytest.MonkeyPatch,
    *,
    db: Db,
    service: StartService,
    permissions: set[str],
    authenticated: bool,
) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.state.workline_start_service = service
    app.include_router(operation_api.router, prefix="/api/v1/workline/operations")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[_get_cache_service] = lambda: None
    if authenticated:
        app.dependency_overrides[require_auth] = lambda: 9

    async def get_permissions(*_args: object, **_kwargs: object) -> set[str]:
        return permissions

    monkeypatch.setattr(rbac, "get_user_permissions", get_permissions)
    return app


def test_start_asgi_contract_enforces_auth_permission_and_version_wire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = "/api/v1/workline/operations/worklines/7/start"
    service = StartService(_line())

    unauthenticated = _asgi_app(
        monkeypatch,
        db=Db(),
        service=service,
        permissions=set(),
        authenticated=False,
    )
    with TestClient(unauthenticated) as client:
        unauthorized = client.post(path, json={"version": 3})
    assert unauthorized.status_code == 401

    permissions: set[str] = set()
    authorized = _asgi_app(
        monkeypatch,
        db=Db(),
        service=service,
        permissions=permissions,
        authenticated=True,
    )
    with TestClient(authorized) as client:
        forbidden = client.post(path, json={"version": 3})
        permissions.add("biz:workline:start")
        replay = client.post(path, json={"version": 3})

    assert forbidden.status_code == 403
    assert replay.status_code == 200
    assert replay.json()["data"] == {
        "workline_id": 7,
        "version": 4,
        "plugin_key": "example_plugin",
        "plugin_version": "1.0",
        "flow_mode": "GENERIC_FLOW",
        "is_active": True,
    }


def test_start_asgi_contract_serializes_stable_error_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _asgi_app(
        monkeypatch,
        db=Db(),
        service=StartService(WorkLineStartInvalidStateError("not stopped")),
        permissions={"biz:workline:start"},
        authenticated=True,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/workline/operations/worklines/7/start",
            json={"version": 3},
        )

    assert response.status_code == 409
    assert response.json()["code"] == "3012"
    assert response.json()["data"] == {"reason": "INVALID_STATE"}


def test_start_asgi_rejects_retired_request_identity(monkeypatch):
    service = StartService(_line())
    app = _asgi_app(monkeypatch, db=Db(), service=service, permissions={"biz:workline:start"}, authenticated=True)
    with TestClient(app) as client:
        response = client.post("/api/v1/workline/operations/worklines/7/start", json={"request_id": "REQUEST-1"})
    assert response.status_code == 422
    assert service.calls == []
