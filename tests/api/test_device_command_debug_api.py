"""DeviceCommand 无业务联调 Swagger API 合同。"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from src.core.uuid7 import new_uuid7
from src.register import register_exception, register_routers


async def _allow_permission() -> None:
    return None


def _runtime() -> SimpleNamespace:
    return SimpleNamespace(
        command_service=SimpleNamespace(
            create_manual_debug_command=AsyncMock(),
            get_command_snapshot=AsyncMock(),
        )
    )


def _app(runtime: SimpleNamespace | None) -> FastAPI:
    app = FastAPI()
    register_exception(app)
    register_routers(app)
    app.state.device_command_runtime = runtime
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/v1/device/commands"):
            continue
        for dependency in route.dependencies:
            app.dependency_overrides[dependency.dependency] = _allow_permission
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


def _permission(route: APIRoute) -> list[str]:
    return [getattr(dependency.dependency, "permission_required", "") for dependency in route.dependencies]


def _payload() -> dict[str, object]:
    return {
        "client_request_id": new_uuid7(),
        "endpoint_base_url": "http://ecs-mock:8080",
        "device_code": "RS-MOCK-PLACEMENT-01",
        "timeout": 30_000,
        "task_type": "PICK_AND_PUT",
        "params": {"target_code": "OUTLET-1"},
    }


def test_device_command_debug_routes_use_separate_permissions_and_onsite_example() -> None:
    app = _app(_runtime())

    create_route = _route(app, "/api/v1/device/commands/debug", "POST")
    read_route = _route(app, "/api/v1/device/commands/{command_code}", "GET")

    assert create_route is not None
    assert read_route is not None
    assert _permission(create_route) == ["ops:device:debug-create"]
    assert _permission(read_route) == ["ops:device:read"]
    examples = app.openapi()["paths"]["/api/v1/device/commands/debug"]["post"]["requestBody"]["content"][
        "application/json"
    ]["examples"]
    assert set(examples) == {"onsite_station_scan1_move_forward"}
    assert examples["onsite_station_scan1_move_forward"]["value"] == {
        "client_request_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4471",
        "endpoint_base_url": "http://10.24.209.26:8080",
        "device_code": "STATION_SCAN1",
        "timeout": 30000,
        "task_type": "MOVE_FORWARD",
        "params": {
            "source": {
                "location_id": "STATION_SCAN1",
                "location_type": "SCAN_PLATFORM",
            }
        },
    }


@pytest.mark.asyncio
async def test_create_debug_command_returns_accepted_handle_and_forwards_frozen_endpoint_contract() -> None:
    runtime = _runtime()
    runtime.command_service.create_manual_debug_command.return_value = SimpleNamespace(
        command_code="CMD-MANUAL-001",
        status="PENDING",
    )
    payload = _payload()

    async with AsyncClient(transport=ASGITransport(app=_app(runtime)), base_url="http://test") as client:
        response = await client.post("/api/v1/device/commands/debug", json=payload)

    assert response.status_code == 202
    assert response.json()["code"] == "1004"
    assert response.json()["data"] == {
        "command_code": "CMD-MANUAL-001",
        "client_request_id": payload["client_request_id"],
        "status": "PENDING",
    }
    runtime.command_service.create_manual_debug_command.assert_awaited_once_with(
        client_request_id=payload["client_request_id"],
        endpoint_base_url=payload["endpoint_base_url"],
        device_code=payload["device_code"],
        command_timeout_ms=payload["timeout"],
        task_type=payload["task_type"],
        params=payload["params"],
        contract_key="third_party_integration",
        contract_version="1.1",
        trace_id=None,
    )


@pytest.mark.asyncio
async def test_create_debug_command_rejects_internal_timeout_field() -> None:
    payload = _payload()
    payload["command_timeout_ms"] = payload.pop("timeout")

    async with AsyncClient(transport=ASGITransport(app=_app(_runtime())), base_url="http://test") as client:
        response = await client.post("/api/v1/device/commands/debug", json=payload)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_debug_command_exposes_lifecycle_and_normalized_callback() -> None:
    runtime = _runtime()
    runtime.command_service.get_command_snapshot.return_value = SimpleNamespace(
        command_code="CMD-MANUAL-001",
        client_request_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4471",
        device_code="RS-MOCK-PLACEMENT-01",
        endpoint_base_url="http://ecs-mock:8080",
        contract_key="rough_sorter.placement_device",
        contract_version="1.0",
        command_timeout_ms=30_000,
        task_type="PICK_AND_PUT",
        params={"target_code": "OUTLET-1"},
        trace_id="TRACE-MANUAL-DEBUG-001",
        status="SUCCEEDED",
        attempt_count=1,
        ack_received_at=datetime(2026, 8, 23, 10, 0, 1),
        completed_at=datetime(2026, 8, 23, 10, 0, 2),
        failure_code=None,
        reconciliation_reason=None,
        callback=SimpleNamespace(
            result="SUCCESS",
            data={"outlet": "OUTLET-1"},
            error_detail=None,
            source_event_id="RESULT-CMD-MANUAL-001",
            received_at=datetime(2026, 8, 23, 10, 0, 2),
            apply_status="APPLIED",
        ),
    )

    async with AsyncClient(transport=ASGITransport(app=_app(runtime)), base_url="http://test") as client:
        response = await client.get("/api/v1/device/commands/CMD-MANUAL-001")

    assert response.status_code == 200
    assert response.json()["code"] == "1000"
    data = response.json()["data"]
    assert data["status"] == "SUCCEEDED"
    assert data["ack_received_at"] == "2026-08-23T10:00:01Z"
    assert data["completed_at"] == "2026-08-23T10:00:02Z"
    assert data["callback"] == {
        "result": "SUCCESS",
        "data": {"outlet": "OUTLET-1"},
        "error_detail": None,
        "source_event_id": "RESULT-CMD-MANUAL-001",
        "received_at": "2026-08-23T10:00:02Z",
        "apply_status": "APPLIED",
    }
    runtime.command_service.get_command_snapshot.assert_awaited_once_with("CMD-MANUAL-001")


@pytest.mark.asyncio
async def test_device_command_debug_routes_fail_closed_without_runtime() -> None:
    async with AsyncClient(transport=ASGITransport(app=_app(None)), base_url="http://test") as client:
        create = await client.post("/api/v1/device/commands/debug", json=_payload())
        read = await client.get("/api/v1/device/commands/CMD-MANUAL-001")

    assert (create.status_code, create.json()["code"]) == (503, "5030")
    assert (read.status_code, read.json()["code"]) == (503, "5030")
