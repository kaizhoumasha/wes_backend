"""DeviceCommand 无业务联调 Swagger API 合同。"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from src.app.device.contracts import EcsDeviceStatus
from src.app.device.ecs_adapter import EcsStatusUnavailableError
from src.core.rbac import require_superuser
from src.core.security import require_auth
from src.core.uuid7 import new_uuid7
from src.register import register_exception, register_routers


async def _allow_superuser(request: Request) -> None:
    request.state.user_id = 42
    request.state.is_superuser = True


def _runtime() -> SimpleNamespace:
    return SimpleNamespace(
        command_service=SimpleNamespace(
            create_manual_debug_command=AsyncMock(),
            preflight_manual_debug=AsyncMock(),
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
            app.dependency_overrides[dependency.dependency] = _allow_superuser
    return app


def _secured_app(runtime: SimpleNamespace, *, is_superuser: bool) -> FastAPI:
    app = FastAPI()
    register_exception(app)
    register_routers(app)
    app.state.device_command_runtime = runtime

    async def authenticate(request: Request) -> int:
        request.state.user_id = 42
        request.state.is_superuser = is_superuser
        return 42

    app.dependency_overrides[require_auth] = authenticate
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
        "reason": "现场供应商联调",
    }


def test_device_command_debug_routes_are_superuser_only_and_keep_onsite_example() -> None:
    app = _app(_runtime())

    preflight_route = _route(app, "/api/v1/device/commands/debug/preflight", "POST")
    create_route = _route(app, "/api/v1/device/commands/debug", "POST")
    read_route = _route(app, "/api/v1/device/commands/{command_code}", "GET")

    assert preflight_route is not None
    assert create_route is not None
    assert read_route is not None
    assert all(
        route.dependencies[0].dependency is require_superuser for route in (preflight_route, create_route, read_route)
    )
    assert _permission(preflight_route) == [""]
    assert _permission(create_route) == [""]
    assert _permission(read_route) == [""]
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
        "reason": "现场供应商联调",
    }


@pytest.mark.asyncio
async def test_non_superuser_cannot_preflight_create_or_read_debug_command() -> None:
    app = _secured_app(_runtime(), is_superuser=False)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        preflight = await client.post(
            "/api/v1/device/commands/debug/preflight",
            json={"endpoint_base_url": "http://ecs-mock:8080"},
        )
        create = await client.post("/api/v1/device/commands/debug", json=_payload())
        detail = await client.get("/api/v1/device/commands/CMD-MANUAL-001")

    assert (preflight.status_code, create.status_code, detail.status_code) == (403, 403, 403)


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
        execution_reason=payload["reason"],
        created_by=42,
    )


@pytest.mark.asyncio
async def test_preflight_returns_all_normalized_device_statuses() -> None:
    runtime = _runtime()
    status = EcsDeviceStatus.model_validate(
        {
            "device": {
                "device_code": "ARM-01",
                "device_name": "机械臂 1",
                "device_type": "ROBOTIC_ARM",
                "role": "PLACEMENT_DEVICE",
                "supported_commands": ["PICK", "MOVE"],
                "supported_events": [],
            },
            "state": {
                "device_code": "ARM-01",
                "mode": "AUTO",
                "status": "IDLE",
                "is_online": True,
                "current_command_code": None,
                "scenario": "success",
                "updated_at": 1_787_475_600_000,
            },
        }
    )
    runtime.command_service.preflight_manual_debug.return_value = SimpleNamespace(
        endpoint_base_url="http://ecs-mock:8080",
        devices=(SimpleNamespace(status=status, rejection_code=None),),
    )

    async with AsyncClient(transport=ASGITransport(app=_app(runtime)), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/device/commands/debug/preflight",
            json={"endpoint_base_url": "http://ECS-MOCK:8080/"},
        )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "endpoint_base_url": "http://ecs-mock:8080",
        "devices": [
            {
                "device": status.device.model_dump(mode="json"),
                "state": status.state.model_dump(mode="json"),
                "admissible": True,
                "rejection_code": None,
            }
        ],
    }
    runtime.command_service.preflight_manual_debug.assert_awaited_once_with("http://ECS-MOCK:8080/")


@pytest.mark.asyncio
async def test_preflight_maps_invalid_endpoint_and_unavailable_ecs() -> None:
    invalid_runtime = _runtime()
    invalid_runtime.command_service.preflight_manual_debug.side_effect = ValueError("endpoint 无效")
    unavailable_runtime = _runtime()
    unavailable_runtime.command_service.preflight_manual_debug.side_effect = EcsStatusUnavailableError("ECS 不可用")

    async with AsyncClient(transport=ASGITransport(app=_app(invalid_runtime)), base_url="http://test") as client:
        invalid = await client.post(
            "/api/v1/device/commands/debug/preflight",
            json={"endpoint_base_url": "http://ecs-mock:8080"},
        )
    async with AsyncClient(transport=ASGITransport(app=_app(unavailable_runtime)), base_url="http://test") as client:
        unavailable = await client.post(
            "/api/v1/device/commands/debug/preflight",
            json={"endpoint_base_url": "http://ecs-mock:8080"},
        )

    assert invalid.status_code == 400
    assert unavailable.status_code == 503


@pytest.mark.asyncio
async def test_create_debug_command_rejects_internal_timeout_field() -> None:
    payload = _payload()
    payload["command_timeout_ms"] = payload.pop("timeout")

    async with AsyncClient(transport=ASGITransport(app=_app(_runtime())), base_url="http://test") as client:
        response = await client.post("/api/v1/device/commands/debug", json=payload)

    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["", "   ", "x" * 501])
async def test_create_debug_command_requires_bounded_nonblank_reason(reason: str) -> None:
    payload = _payload()
    payload["reason"] = reason

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
        execution_reason="现场供应商联调",
        created_by=42,
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
    assert data["execution_reason"] == "现场供应商联调"
    assert data["created_by"] == 42
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
