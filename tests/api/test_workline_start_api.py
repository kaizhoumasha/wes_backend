"""WorkLine START API 的事务、响应与 post-commit wakeup 合同。"""

from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from src.app.workline.models.line_run_epoch import LineRunEpoch, LineRunEpochStatus
from src.app.workline.services.workline_start_service import (
    WorkLineStartConfigurationError,
    WorkLineStartIdempotencyConflictError,
    WorkLineStartInvalidStateError,
    WorkLineStartNotFoundError,
    WorkLineStartResult,
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
    def __init__(self, result: WorkLineStartResult | Exception) -> None:
        self.result = result
        self.calls: list[tuple[int, str]] = []

    async def start(self, _db: object, *, workline_id: int, request_id: str) -> WorkLineStartResult:
        self.calls.append((workline_id, request_id))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class Queue:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    def enqueue_outbox(self, **kwargs: object) -> None:
        assert kwargs["limit"] == 50
        self.calls += 1
        if self.error is not None:
            raise self.error


def _epoch(*, status: LineRunEpochStatus = LineRunEpochStatus.ACTIVE) -> LineRunEpoch:
    return LineRunEpoch(
        id=31,
        epoch_code="REQUEST-1",
        workline_id=7,
        plugin_key="example_plugin",
        plugin_version="1.0",
        flow_mode="GENERIC_FLOW",
        topology_digest="a" * 64,
        configuration_digest="b" * 64,
        configuration_snapshot_json={},
        status=status,
        started_at=datetime(2026, 8, 19, 9),
        closed_at=datetime(2026, 8, 19, 10) if status is LineRunEpochStatus.CLOSED else None,
    )


def _request(service: object | None, queue: object | None) -> object:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(workline_start_service=service, task_queue_gateway=queue))
    )


def test_start_route_replaces_sandbox_contract_and_uses_dedicated_permission() -> None:
    routes = {getattr(route, "path", None): route for route in operation_api.router.routes}

    assert "/sandbox/worklines/{workline_id}/start" not in routes
    route = routes["/worklines/{workline_id}/start"]
    permissions = [
        getattr(getattr(dependency, "dependency", None), "permission_required", None)
        for dependency in route.dependencies
    ]
    assert permissions == ["biz:workline:start"]


def test_start_request_is_closed_and_normalizes_stable_identity() -> None:
    payload = operation_api.WorkLineStartRequest(request_id="  REQUEST-1  ")
    assert payload.request_id == "REQUEST-1"
    with pytest.raises(ValueError):
        operation_api.WorkLineStartRequest(request_id=" ")
    with pytest.raises(ValueError):
        operation_api.WorkLineStartRequest(request_id="x" * 101)
    with pytest.raises(ValueError):
        operation_api.WorkLineStartRequest(request_id="REQUEST-1", trace_id="legacy")


@pytest.mark.asyncio
async def test_start_commits_once_then_wakes_system_outbox_once() -> None:
    service = StartService(
        WorkLineStartResult(
            epoch=_epoch(),
            current_workline_runtime_status="READY",
            created=True,
            released_outbox_count=2,
        )
    )
    queue = Queue()
    db = Db()

    body = await operation_api.start_workline(
        workline_id=7,
        payload=operation_api.WorkLineStartRequest(request_id="REQUEST-1"),
        request=_request(service, queue),  # type: ignore[arg-type]
        response=Response(),
        db=db,  # type: ignore[arg-type]
    )

    assert db.commits == 1
    assert queue.calls == 1
    assert service.calls == [(7, "REQUEST-1")]
    assert body["code"] == "1000"
    assert body["data"] == {
        "line_run_epoch_id": 31,
        "epoch_code": "REQUEST-1",
        "workline_id": 7,
        "plugin_key": "example_plugin",
        "plugin_version": "1.0",
        "flow_mode": "GENERIC_FLOW",
        "epoch_status": "ACTIVE",
        "epoch_started_at": "2026-08-19T09:00:00",
        "epoch_closed_at": None,
        "current_workline_runtime_status": "READY",
        "created": True,
    }


@pytest.mark.asyncio
async def test_start_commit_failure_rolls_back_and_never_wakes() -> None:
    service = StartService(WorkLineStartResult(_epoch(), "READY", True, 1))
    queue = Queue()
    db = Db(commit_error=RuntimeError("commit failed"))

    with pytest.raises(RuntimeError, match="commit failed"):
        await operation_api.start_workline(
            workline_id=7,
            payload=operation_api.WorkLineStartRequest(request_id="REQUEST-1"),
            request=_request(service, queue),  # type: ignore[arg-type]
            response=Response(),
            db=db,  # type: ignore[arg-type]
        )

    assert db.commits == 1
    assert db.rollbacks >= 1
    assert queue.calls == 0


@pytest.mark.asyncio
async def test_start_wakeup_failure_keeps_committed_success() -> None:
    service = StartService(WorkLineStartResult(_epoch(), "READY", True, 1))
    queue = Queue(RuntimeError("broker unavailable"))
    db = Db()

    body = await operation_api.start_workline(
        workline_id=7,
        payload=operation_api.WorkLineStartRequest(request_id="REQUEST-1"),
        request=_request(service, queue),  # type: ignore[arg-type]
        response=Response(),
        db=db,  # type: ignore[arg-type]
    )

    assert db.commits == 1
    assert queue.calls == 1
    assert body["code"] == "1000"


@pytest.mark.asyncio
async def test_start_service_missing_returns_503_without_commit() -> None:
    db = Db()
    http_response = Response()

    body = await operation_api.start_workline(
        workline_id=7,
        payload=operation_api.WorkLineStartRequest(request_id="REQUEST-1"),
        request=_request(None, Queue()),  # type: ignore[arg-type]
        response=http_response,
        db=db,  # type: ignore[arg-type]
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
        (WorkLineStartIdempotencyConflictError("owned elsewhere"), 409, "3012", "IDEMPOTENCY_CONFLICT"),
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
        payload=operation_api.WorkLineStartRequest(request_id="REQUEST-1"),
        request=_request(StartService(error), Queue()),  # type: ignore[arg-type]
        response=http_response,
        db=db,  # type: ignore[arg-type]
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
    app.state.task_queue_gateway = Queue()
    app.include_router(operation_api.router, prefix="/api/v1/workline/operations")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[_get_cache_service] = lambda: None
    if authenticated:
        app.dependency_overrides[require_auth] = lambda: 9

    async def get_permissions(*_args: object, **_kwargs: object) -> set[str]:
        return permissions

    monkeypatch.setattr(rbac, "get_user_permissions", get_permissions)
    return app


def test_start_asgi_contract_enforces_auth_permission_and_closed_replay_wire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = "/api/v1/workline/operations/worklines/7/start"
    closed = _epoch(status=LineRunEpochStatus.CLOSED)
    service = StartService(WorkLineStartResult(closed, None, False, 0))

    unauthenticated = _asgi_app(
        monkeypatch,
        db=Db(),
        service=service,
        permissions=set(),
        authenticated=False,
    )
    with TestClient(unauthenticated) as client:
        unauthorized = client.post(path, json={"request_id": "REQUEST-1"})
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
        forbidden = client.post(path, json={"request_id": "REQUEST-1"})
        permissions.add("biz:workline:start")
        replay = client.post(path, json={"request_id": " REQUEST-1 "})

    assert forbidden.status_code == 403
    assert replay.status_code == 200
    assert replay.json()["data"] == {
        "line_run_epoch_id": 31,
        "epoch_code": "REQUEST-1",
        "workline_id": 7,
        "plugin_key": "example_plugin",
        "plugin_version": "1.0",
        "flow_mode": "GENERIC_FLOW",
        "epoch_status": "CLOSED",
        "epoch_started_at": "2026-08-19T09:00:00",
        "epoch_closed_at": "2026-08-19T10:00:00",
        "current_workline_runtime_status": None,
        "created": False,
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
            json={"request_id": "REQUEST-ERROR"},
        )

    assert response.status_code == 409
    assert response.json()["code"] == "3012"
    assert response.json()["data"] == {"reason": "INVALID_STATE"}


def test_start_asgi_closed_replay_returns_current_ready_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    service = StartService(WorkLineStartResult(_epoch(status=LineRunEpochStatus.CLOSED), "READY", False, 0))
    app = _asgi_app(
        monkeypatch,
        db=Db(),
        service=service,
        permissions={"biz:workline:start"},
        authenticated=True,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/workline/operations/worklines/7/start",
            json={"request_id": "REQUEST-1"},
        )

    assert response.status_code == 200
    assert response.json()["data"]["epoch_status"] == "CLOSED"
    assert response.json()["data"]["current_workline_runtime_status"] == "READY"
    assert response.json()["data"]["created"] is False


def test_start_asgi_missing_queue_port_keeps_committed_success(monkeypatch: pytest.MonkeyPatch) -> None:
    service = StartService(WorkLineStartResult(_epoch(), "READY", True, 1))
    module_fallback = Queue()
    monkeypatch.setattr(operation_api, "task_queue_gateway", module_fallback)
    app = _asgi_app(
        monkeypatch,
        db=Db(),
        service=service,
        permissions={"biz:workline:start"},
        authenticated=True,
    )
    del app.state.task_queue_gateway

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/workline/operations/worklines/7/start",
            json={"request_id": "REQUEST-1"},
        )

    assert response.status_code == 200
    assert response.json()["data"]["created"] is True
    assert module_fallback.calls == 0
