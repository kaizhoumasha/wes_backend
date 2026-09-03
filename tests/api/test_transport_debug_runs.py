"""Transport 自动联调轮次 API 与 SSE 合同。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from src.app.sys.services.event_stream_service import TRANSPORT_DEBUG_RUN_STREAM_CHANNEL
from src.app.transport.debug_run_contracts import (
    TransportDebugBinSelection,
    TransportDebugFaceGroup,
    TransportDebugRunPhase,
    TransportDebugRunStatus,
)
from src.app.transport.debug_run_service import (
    TransportDebugRunConflict,
    TransportDebugRunContractError,
    TransportDebugRunPage,
    TransportDebugRunSnapshot,
)
from src.core.exceptions import NotFoundException
from src.register import register_exception, register_routers


def _snapshot(*, run_id: str = "debug-run-1", face: str = " 90 ") -> TransportDebugRunSnapshot:
    return TransportDebugRunSnapshot(
        run_id=run_id,
        status=TransportDebugRunStatus.RUNNING,
        rack_id="510056",
        face_groups=(
            TransportDebugFaceGroup(
                face=face,
                bins=(TransportDebugBinSelection("A000001922", "510056A3F2C101"),),
            ),
        ),
        current_group_index=0,
        current_phase=TransportDebugRunPhase.RACK_TO_STATION,
        current_step=None,
        observed_bin_ids=(),
        attention_code=None,
        attention_detail=None,
        can_abort=False,
        version=1,
        created_by_user_id=42,
        aborted_by_user_id=None,
        aborted_reason=None,
        created_at="2026-09-02T12:00:00+00:00",
        updated_at="2026-09-02T12:00:00+00:00",
    )


def _service() -> SimpleNamespace:
    snapshot = _snapshot()
    return SimpleNamespace(
        create_run=AsyncMock(return_value=snapshot),
        get_run=AsyncMock(return_value=snapshot),
        list_runs=AsyncMock(return_value=TransportDebugRunPage(items=(snapshot,), next_cursor=None)),
        abort_run=AsyncMock(return_value=snapshot),
    )


async def _allow_permission(request: Request) -> None:
    request.state.user_id = 42


def _app(service: SimpleNamespace | None) -> FastAPI:
    app = FastAPI()
    register_exception(app)
    register_routers(app)
    app.state.transport_runtime = None if service is None else SimpleNamespace(closed=False, debug_run_service=service)
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/v1/transport/debug-runs"):
            continue
        for dependency in route.dependencies:
            app.dependency_overrides[dependency.dependency] = _allow_permission
    return app


def _route(path: str, method: str) -> APIRoute:
    app = _app(_service())
    route = next(
        (item for item in app.routes if isinstance(item, APIRoute) and item.path == path and method in item.methods),
        None,
    )
    assert route is not None
    return route


def _permission(route: APIRoute) -> list[str]:
    return [getattr(item.dependency, "permission_required", "") for item in route.dependencies]


def _payload(*, face: str = " 90 ") -> dict[str, object]:
    return {
        "rack_id": "510056",
        "face_groups": [
            {
                "face": face,
                "bins": [{"bin_id": "A000001922", "slot_id": "510056A3F2C101"}],
            }
        ],
    }


def test_debug_run_routes_use_five_unique_permissions_and_static_stream_precedes_detail() -> None:
    assert _permission(_route("/api/v1/transport/debug-runs", "POST")) == ["ops:transport-debug-run:start"]
    assert _permission(_route("/api/v1/transport/debug-runs", "GET")) == ["ops:transport-debug-run:list"]
    assert _permission(_route("/api/v1/transport/debug-runs/stream", "GET")) == ["ops:transport-debug-run:stream"]
    assert _permission(_route("/api/v1/transport/debug-runs/{run_id}", "GET")) == ["ops:transport-debug-run:read"]
    assert _permission(_route("/api/v1/transport/debug-runs/{run_id}/abort", "POST")) == [
        "ops:transport-debug-run:abort"
    ]

    app = _app(_service())
    paths = [item.path for item in app.routes if isinstance(item, APIRoute)]
    assert paths.index("/api/v1/transport/debug-runs/stream") < paths.index("/api/v1/transport/debug-runs/{run_id}")
    response_content = app.openapi()["paths"]["/api/v1/transport/debug-runs/stream"]["get"]["responses"]["200"][
        "content"
    ]
    assert response_content == {"text/event-stream": {"schema": {"type": "string"}}}


@pytest.mark.asyncio
async def test_create_debug_run_preserves_face_and_passes_authenticated_actor() -> None:
    service = _service()
    async with AsyncClient(transport=ASGITransport(app=_app(service)), base_url="http://test") as client:
        response = await client.post("/api/v1/transport/debug-runs", json=_payload())

    assert response.status_code == 202
    assert response.json()["code"] == "1004"
    assert response.json()["data"]["face_groups"][0]["face"] == " 90 "
    request = service.create_run.await_args.args[0]
    assert request.face_groups[0].face == " 90 "
    assert service.create_run.await_args.kwargs == {"actor_id": 42}


@pytest.mark.asyncio
async def test_list_read_and_abort_delegate_only_to_debug_run_service() -> None:
    service = _service()
    app = _app(service)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/api/v1/transport/debug-runs", params={"limit": 25, "cursor": "next"})
        read = await client.get("/api/v1/transport/debug-runs/debug-run-1")
        aborted = await client.post(
            "/api/v1/transport/debug-runs/debug-run-1/abort",
            json={
                "assertion": "PHYSICAL_STATE_VERIFIED",
                "reason": "现场确认全部机构静止",
            },
        )

    assert listed.status_code == read.status_code == aborted.status_code == 200
    service.list_runs.assert_awaited_once_with(limit=25, cursor="next")
    service.get_run.assert_awaited_once_with("debug-run-1")
    service.abort_run.assert_awaited_once_with(
        "debug-run-1",
        assertion="PHYSICAL_STATE_VERIFIED",
        reason="现场确认全部机构静止",
        actor_id=42,
    )


@pytest.mark.asyncio
async def test_debug_run_api_maps_domain_failures_and_missing_runtime() -> None:
    conflict_service = _service()
    conflict_service.create_run.side_effect = TransportDebugRunConflict("active")
    contract_service = _service()
    contract_service.list_runs.side_effect = TransportDebugRunContractError("bad cursor")
    missing_service = _service()
    missing_service.get_run.side_effect = NotFoundException(
        resource_type="TransportDebugRun",
        resource_id="missing",
    )

    async with AsyncClient(transport=ASGITransport(app=_app(conflict_service)), base_url="http://test") as client:
        conflict = await client.post("/api/v1/transport/debug-runs", json=_payload())
    async with AsyncClient(transport=ASGITransport(app=_app(contract_service)), base_url="http://test") as client:
        contract = await client.get("/api/v1/transport/debug-runs", params={"cursor": "bad"})
    async with AsyncClient(transport=ASGITransport(app=_app(missing_service)), base_url="http://test") as client:
        missing = await client.get("/api/v1/transport/debug-runs/missing")
    async with AsyncClient(transport=ASGITransport(app=_app(None)), base_url="http://test") as client:
        unavailable = await client.get("/api/v1/transport/debug-runs")

    assert (conflict.status_code, conflict.json()["code"]) == (409, "3012")
    assert (contract.status_code, contract.json()["code"]) == (400, "2004")
    assert (missing.status_code, missing.json()["code"]) == (404, "3000")
    assert (unavailable.status_code, unavailable.json()["code"]) == (503, "5030")


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [_payload(face="   "), {**_payload(), "unexpected": True}])
async def test_debug_run_create_rejects_blank_face_and_unknown_fields_before_service(
    payload: dict[str, object],
) -> None:
    service = _service()
    async with AsyncClient(transport=ASGITransport(app=_app(service)), base_url="http://test") as client:
        response = await client.post("/api/v1/transport/debug-runs", json=payload)

    assert response.status_code == 422
    service.create_run.assert_not_awaited()


class _FakeStreamService:
    def __init__(self, events: tuple[dict[str, object] | None, ...]) -> None:
        self.events = events
        self.subscriptions: list[tuple[str, float]] = []

    async def subscribe(self, channel: str, *, timeout_seconds: float):
        self.subscriptions.append((channel, timeout_seconds))
        for event in self.events:
            yield event


@pytest.mark.asyncio
async def test_debug_run_stream_uses_dedicated_channel_heartbeat_and_closed_payload() -> None:
    stream = _FakeStreamService(
        (
            None,
            {"type": "unknown", "payload": {}, "timestamp": 1},
            {
                "type": "transport_debug_run.updated",
                "payload": {
                    "run_id": "debug-run-1",
                    "version": 2,
                    "status": "RUNNING",
                    "updated_at": "2026-09-02T12:00:01+00:00",
                    "secret": "must-be-rejected",
                },
                "timestamp": 2,
            },
            {
                "type": "transport_debug_run.updated",
                "payload": {
                    "run_id": "debug-run-1",
                    "version": 2,
                    "status": "RUNNING",
                    "updated_at": "2026-09-02T12:00:01+00:00",
                },
                "timestamp": 3,
            },
        )
    )
    app = _app(_service())
    app.state.transport_event_stream_service = stream
    route = next(
        item for item in app.routes if isinstance(item, APIRoute) and item.path == "/api/v1/transport/debug-runs/stream"
    )
    request = Request({"type": "http", "method": "GET", "path": "/stream", "headers": [], "app": app})

    response = await route.endpoint(request)
    heartbeat = await anext(response.body_iterator)
    event = await anext(response.body_iterator)
    await response.body_iterator.aclose()

    assert stream.subscriptions == [(TRANSPORT_DEBUG_RUN_STREAM_CHANNEL, 25.0)]
    assert heartbeat == ": heartbeat\n\n"
    assert "event: transport_debug_run.updated\n" in event
    assert '"version": 2' in event
    assert "secret" not in event
    assert "id:" not in event
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
