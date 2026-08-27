"""Device EVENT 阻塞对账 API 合同。"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from src.app.device.ecs_adapter import EcsStatusUnavailableError
from src.app.device.services.device_command_admission import DeviceCommandAdmissionError
from src.app.device.services.device_command_service import (
    DeviceCommandManualReconciliationConflictError,
    DeviceCommandManualReconciliationNotFoundError,
)
from src.app.device.services.device_evidence_service import (
    EventCommandBlockConflictError,
    EventCommandBlockNotFoundError,
)
from src.core.rbac import require_superuser
from src.register import register_exception, register_routers


async def _allow_superuser(request: Request) -> None:
    request.state.user_id = 42
    request.state.is_superuser = True


def _app(service: object, *, evidence_service: object | None = None) -> FastAPI:
    app = FastAPI()
    register_exception(app)
    register_routers(app)
    app.state.device_command_runtime = SimpleNamespace(
        command_service=service,
        evidence_service=evidence_service or service,
    )
    for route in app.routes:
        if not isinstance(route, APIRoute) or "/evidences/" not in route.path:
            continue
        for dependency in route.dependencies:
            app.dependency_overrides[dependency.dependency] = _allow_superuser
    return app


def _route(app: FastAPI, path: str, method: str) -> APIRoute | None:
    return next(
        (
            route
            for route in app.routes
            if isinstance(route, APIRoute) and route.path == path and method in route.methods
        ),
        None,
    )


def test_manual_reconciliation_route_is_superuser_only() -> None:
    app = _app(SimpleNamespace(reconcile_delivery_unknown_as_device_idle=AsyncMock()))
    route = _route(
        app,
        "/api/v1/device/evidences/{source_event_id}/blockers/{block_id}/reconcile-device-idle",
        "POST",
    )

    assert route is not None
    assert route.dependencies[0].dependency is require_superuser


@pytest.mark.asyncio
async def test_manual_reconciliation_api_returns_failed_terminal_fact() -> None:
    service = SimpleNamespace(reconcile_delivery_unknown_as_device_idle=AsyncMock())
    service.reconcile_delivery_unknown_as_device_idle.return_value = SimpleNamespace(
        command_code="CMD-001", status="FAILED"
    )

    async with AsyncClient(transport=ASGITransport(app=_app(service)), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/device/evidences/EVENT-001/blockers/51/reconcile-device-idle",
            json={"reason": "  现场确认设备空闲  "},
        )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "command_code": "CMD-001",
        "status": "FAILED",
        "failure_code": "MANUAL_RECONCILIATION_DEVICE_IDLE",
    }
    service.reconcile_delivery_unknown_as_device_idle.assert_awaited_once_with(
        source_event_id="EVENT-001",
        block_id=51,
        reason="现场确认设备空闲",
        actor_id=42,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (DeviceCommandManualReconciliationNotFoundError("missing"), 404),
        (DeviceCommandManualReconciliationConflictError("conflict"), 409),
        (DeviceCommandAdmissionError("DEVICE_OFFLINE"), 409),
        (EcsStatusUnavailableError("unavailable"), 503),
    ],
)
async def test_manual_reconciliation_api_maps_service_failures(error: Exception, expected_status: int) -> None:
    service = SimpleNamespace(reconcile_delivery_unknown_as_device_idle=AsyncMock(side_effect=error))

    async with AsyncClient(transport=ASGITransport(app=_app(service)), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/device/evidences/EVENT-001/blockers/51/reconcile-device-idle",
            json={"reason": "确认空闲"},
        )

    assert response.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["", "   ", "x" * 501])
async def test_manual_reconciliation_api_requires_bounded_nonblank_reason(reason: str) -> None:
    service = SimpleNamespace(reconcile_delivery_unknown_as_device_idle=AsyncMock())

    async with AsyncClient(transport=ASGITransport(app=_app(service)), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/device/evidences/EVENT-001/blockers/51/reconcile-device-idle",
            json={"reason": reason},
        )

    assert response.status_code == 422
    service.reconcile_delivery_unknown_as_device_idle.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconciliation_api_reports_missing_runtime_as_unavailable() -> None:
    app = _app(SimpleNamespace())
    del app.state.device_command_runtime

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/device/evidences/EVENT-001/blocker")

    assert response.status_code == 503


def test_blocker_query_and_reprocess_routes_are_superuser_only() -> None:
    service = SimpleNamespace(
        get_event_command_block=AsyncMock(),
        reprocess_blocked_event=AsyncMock(),
    )
    app = _app(service)

    query_route = _route(app, "/api/v1/device/evidences/{source_event_id}/blocker", "GET")
    reprocess_route = _route(
        app,
        "/api/v1/device/evidences/{source_event_id}/blockers/{block_id}/reprocess",
        "POST",
    )

    assert query_route is not None
    assert query_route.dependencies[0].dependency is require_superuser
    assert reprocess_route is not None
    assert reprocess_route.dependencies[0].dependency is require_superuser


@pytest.mark.asyncio
async def test_blocker_query_returns_latest_persisted_history_with_fixed_action_paths() -> None:
    service = SimpleNamespace(get_event_command_block=AsyncMock())
    service.get_event_command_block.return_value = SimpleNamespace(
        block_id=51,
        status="REQUEUED",
        source_event_id="EVENT-001",
        device_code="ARM-01",
        blocking_command_code="CMD-001",
        blocking_command_detected_status="RECONCILING",
        blocking_command_detected_reconciliation_reason="DELIVERY_UNKNOWN",
        blocking_command_current_status="FAILED",
        blocking_command_terminal=True,
        reason_code="DEVICE_HAS_ACTIVE_COMMAND",
        blocked_at=datetime(2026, 8, 27, 10, 0),
        requeued_at=datetime(2026, 8, 27, 10, 1),
        reconcile_device_idle_path=("/api/v1/device/evidences/EVENT-001/blockers/51/reconcile-device-idle"),
        reprocess_path="/api/v1/device/evidences/EVENT-001/blockers/51/reprocess",
    )

    async with AsyncClient(transport=ASGITransport(app=_app(service)), base_url="http://test") as client:
        response = await client.get("/api/v1/device/evidences/EVENT-001/blocker")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "block_id": 51,
        "status": "REQUEUED",
        "source_event_id": "EVENT-001",
        "device_code": "ARM-01",
        "blocking_command_code": "CMD-001",
        "blocking_command_detected_status": "RECONCILING",
        "blocking_command_detected_reconciliation_reason": "DELIVERY_UNKNOWN",
        "blocking_command_current_status": "FAILED",
        "blocking_command_terminal": True,
        "reason_code": "DEVICE_HAS_ACTIVE_COMMAND",
        "blocked_at": "2026-08-27T10:00:00Z",
        "requeued_at": "2026-08-27T10:01:00Z",
        "reconcile_device_idle_path": ("/api/v1/device/evidences/EVENT-001/blockers/51/reconcile-device-idle"),
        "reprocess_path": "/api/v1/device/evidences/EVENT-001/blockers/51/reprocess",
    }


@pytest.mark.asyncio
async def test_reprocess_api_returns_accepted_without_claiming_command_or_physical_completion() -> None:
    service = SimpleNamespace(reprocess_blocked_event=AsyncMock())
    service.reprocess_blocked_event.return_value = SimpleNamespace(
        source_event_id="EVENT-001",
        block_id=51,
        apply_status="PENDING",
    )

    async with AsyncClient(transport=ASGITransport(app=_app(service)), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/device/evidences/EVENT-001/blockers/51/reprocess",
            json={"reason": "  Result 已确认，重新处理  "},
        )

    assert response.status_code == 202
    assert response.json()["data"] == {
        "source_event_id": "EVENT-001",
        "block_id": 51,
        "apply_status": "PENDING",
    }
    service.reprocess_blocked_event.assert_awaited_once_with(
        source_event_id="EVENT-001",
        block_id=51,
        reason="Result 已确认，重新处理",
        actor_id=42,
    )


@pytest.mark.asyncio
async def test_reprocess_api_requires_bounded_nonblank_reason() -> None:
    service = SimpleNamespace(reprocess_blocked_event=AsyncMock())

    async with AsyncClient(transport=ASGITransport(app=_app(service)), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/device/evidences/EVENT-001/blockers/51/reprocess",
            json={"reason": "   "},
        )

    assert response.status_code == 422
    service.reprocess_blocked_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (EventCommandBlockNotFoundError("missing"), 404),
        (EventCommandBlockConflictError("conflict"), 409),
    ],
)
async def test_blocker_query_and_reprocess_map_causal_failures(error: Exception, expected_status: int) -> None:
    query = SimpleNamespace(get_event_command_block=AsyncMock(side_effect=error))
    reprocess = SimpleNamespace(reprocess_blocked_event=AsyncMock(side_effect=error))

    async with AsyncClient(transport=ASGITransport(app=_app(query)), base_url="http://test") as client:
        query_response = await client.get("/api/v1/device/evidences/EVENT-001/blocker")
    async with AsyncClient(transport=ASGITransport(app=_app(reprocess)), base_url="http://test") as client:
        reprocess_response = await client.post(
            "/api/v1/device/evidences/EVENT-001/blockers/51/reprocess",
            json={"reason": "重新处理"},
        )

    assert query_response.status_code == expected_status
    assert reprocess_response.status_code == expected_status
