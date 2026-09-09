from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from src.app.workline_integration_debug.v1 import router
from src.register import register_exception


async def _allow(request: Request) -> None:
    request.state.user_id = 42


def _snapshot() -> dict[str, object]:
    return {
        "run_id": "run-1",
        "workline_id": 3,
        "workline_code": "sorting-3",
        "scenario_key": "manual_outbound_picking@v1",
        "expected_plugin_key": "manual_bin_processing",
        "profile": "CONTRACT_SIMULATION",
        "environment_label": "integration",
        "operator_user_id": 42,
        "status": "WAITING_TASK",
        "current_phase": "BIND_TASK",
        "version": 0,
        "task_id": None,
        "issued_operation_id": None,
        "bin_code": None,
        "device_code": "SIM-ECS-01",
        "rack_id": None,
        "plan_resources": None,
        "site_configuration": {},
        "operation_context": {},
        "attention_code": None,
        "attention_detail": None,
        "wms_cleanup_confirmed": False,
        "site_cleanup_confirmed": False,
        "created_at": "2026-09-08T00:00:00Z",
        "updated_at": "2026-09-08T00:00:00Z",
        "steps": [],
    }


def _service() -> SimpleNamespace:
    snapshot = _snapshot()
    return SimpleNamespace(
        create_run=AsyncMock(return_value=snapshot),
        list_runs=AsyncMock(return_value=[snapshot]),
        get_run=AsyncMock(return_value=snapshot),
        send_task_prepare=AsyncMock(return_value=snapshot),
        retry_wms_action=AsyncMock(return_value=snapshot),
        refresh_plan_resources=AsyncMock(return_value=snapshot),
        send_bin_inbound_batch=AsyncMock(return_value=snapshot),
        send_bin_return_batch=AsyncMock(return_value=snapshot),
        send_rack_departure=AsyncMock(return_value=snapshot),
        send_task_completion_confirm=AsyncMock(return_value=snapshot),
        create_transport_action=AsyncMock(return_value=snapshot),
        create_device_action=AsyncMock(return_value=snapshot),
        refresh_device_action=AsyncMock(return_value=snapshot),
        refresh_transport_action=AsyncMock(return_value=snapshot),
        close_run=AsyncMock(return_value=snapshot),
    )


def _app(service: SimpleNamespace | None) -> FastAPI:
    app = FastAPI()
    register_exception(app)
    app.include_router(router, prefix="/api")
    app.state.workline_integration_debug_runtime = None if service is None else SimpleNamespace(service=service)
    for route_item in app.routes:
        if isinstance(route_item, APIRoute) and route_item.path.startswith("/api/v1/workline-integration-debug"):
            for dependency in route_item.dependencies:
                app.dependency_overrides[dependency.dependency] = _allow
    return app


def test_routes_use_endpoint_permissions_required_by_the_permission_catalog() -> None:
    app = _app(_service())
    permissions = {
        getattr(dependency.dependency, "permission_required", "")
        for route_item in app.routes
        if isinstance(route_item, APIRoute) and route_item.path.startswith("/api/v1/workline-integration-debug")
        for dependency in route_item.dependencies
        if getattr(dependency.dependency, "permission_required", "")
    }

    assert permissions == {
        "ops:workline-integration-debug:create",
        "ops:workline-integration-debug:list",
        "ops:workline-integration-debug:stream",
        "ops:workline-integration-debug:read",
        "ops:workline-integration-debug:bind-task",
        "ops:workline-integration-debug:prepare-task",
        "ops:workline-integration-debug:refresh-plan",
        "ops:workline-integration-debug:point2-scan",
        "ops:workline-integration-debug:work-admission",
        "ops:workline-integration-debug:bin-inbound-batch",
        "ops:workline-integration-debug:bin-return-batch",
        "ops:workline-integration-debug:rack-departure",
        "ops:workline-integration-debug:task-completion",
        "ops:workline-integration-debug:refresh-wms",
        "ops:workline-integration-debug:retry-wms",
        "ops:workline-integration-debug:bind-completion",
        "ops:workline-integration-debug:completion-apply-report",
        "ops:workline-integration-debug:transport",
        "ops:workline-integration-debug:refresh-transport",
        "ops:workline-integration-debug:device-command",
        "ops:workline-integration-debug:refresh-device",
        "ops:workline-integration-debug:confirm-phase",
        "ops:workline-integration-debug:complete",
        "ops:workline-integration-debug:takeover",
        "ops:workline-integration-debug:close",
        "ops:workline-integration-debug:export",
    }
    debug_routes = [
        route_item
        for route_item in app.routes
        if isinstance(route_item, APIRoute) and route_item.path.startswith("/api/v1/workline-integration-debug")
    ]
    assert debug_routes
    assert all(
        any(getattr(dependency.dependency, "is_superuser", False) for dependency in route_item.dependencies)
        for route_item in debug_routes
    )


@pytest.mark.asyncio
async def test_create_run_freezes_scenario_without_accepting_device_endpoint() -> None:
    service = _service()
    payload = {
        "workline_code": "sorting-3",
        "profile": "CONTRACT_SIMULATION",
        "environment_label": "integration",
        "device_code": "SIM-ECS-01",
    }
    async with AsyncClient(transport=ASGITransport(app=_app(service)), base_url="http://test") as client:
        created = await client.post("/api/v1/workline-integration-debug/runs", json=payload)
        invalid = await client.post(
            "/api/v1/workline-integration-debug/runs",
            json={**payload, "endpoint_base_url": "http://unreviewed.example"},
        )

    assert created.status_code == 202
    request = service.create_run.await_args.args[0]
    assert request.workline_code == "sorting-3"
    assert request.profile.value == "CONTRACT_SIMULATION"
    assert invalid.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("workline_code", "w" * 51),
        ("environment_label", "e" * 81),
        ("device_code", "d" * 101),
        ("rack_id", "r" * 101),
    ],
)
async def test_create_run_rejects_values_longer_than_the_persisted_columns(field: str, value: str) -> None:
    service = _service()
    payload = {
        "workline_code": "sorting-3",
        "profile": "CONTRACT_SIMULATION",
        "environment_label": "integration",
        "device_code": "SIM-ECS-01",
        field: value,
    }

    async with AsyncClient(transport=ASGITransport(app=_app(service)), base_url="http://test") as client:
        response = await client.post("/api/v1/workline-integration-debug/runs", json=payload)

    assert response.status_code == 422
    service.create_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_transport_action_passes_frozen_site_values_and_authenticated_actor() -> None:
    service = _service()
    payload = {
        "expected_version": 0,
        "client_request_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
        "kind": "MOVE_BINS",
        "rack_id": "RACK-01",
        "bin_code": "BIN-001",
        "source": {"kind": "RACK_BIN_SLOT", "rack_id": "RACK-01", "rack_face": "90", "slot_id": "SLOT-01"},
        "target": {"kind": "HANDOFF_POSITION", "location_code": "LINE3-INFEED"},
        "rcs_template_id": "CTU01",
    }
    async with AsyncClient(transport=ASGITransport(app=_app(service)), base_url="http://test") as client:
        response = await client.post("/api/v1/workline-integration-debug/runs/run-1/transport", json=payload)

    assert response.status_code == 202
    action = service.create_transport_action.await_args.kwargs["action"]
    assert action.bin_code == "BIN-001"
    assert action.source["slot_id"] == "SLOT-01"
    assert service.create_transport_action.await_args.kwargs["actor_id"] == 42


@pytest.mark.asyncio
async def test_device_action_passes_the_selected_sorting3_station() -> None:
    service = _service()
    payload = {
        "expected_version": 0,
        "client_request_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4473",
        "device_code": "STATION_SCAN11",
        "task_type": "MOVE_FORWARD",
        "params": {"location_id": "STATION_SCAN11"},
        "timeout_ms": 30000,
        "reason": "sorting-3 联调",
    }
    async with AsyncClient(transport=ASGITransport(app=_app(service)), base_url="http://test") as client:
        response = await client.post("/api/v1/workline-integration-debug/runs/run-1/device-command", json=payload)

    assert response.status_code == 202
    assert service.create_device_action.await_args.kwargs["device_code"] == "STATION_SCAN11"


@pytest.mark.asyncio
async def test_device_refresh_passes_the_original_client_identity() -> None:
    service = _service()
    payload = {
        "expected_version": 0,
        "client_request_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4473",
    }
    async with AsyncClient(transport=ASGITransport(app=_app(service)), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/workline-integration-debug/runs/run-1/device-command/refresh",
            json=payload,
        )

    assert response.status_code == 200
    assert service.refresh_device_action.await_args.kwargs["client_request_id"] == payload["client_request_id"]


@pytest.mark.asyncio
async def test_wms_retry_requires_explicit_non_receipt_confirmation_and_passes_original_action_identity() -> None:
    service = _service()
    payload = {
        "expected_version": 0,
        "client_request_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4473",
        "wms_non_receipt_confirmed": True,
    }
    async with AsyncClient(transport=ASGITransport(app=_app(service)), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/workline-integration-debug/runs/run-1/wms/retry",
            json=payload,
        )
        invalid = await client.post(
            "/api/v1/workline-integration-debug/runs/run-1/wms/retry",
            json={**payload, "wms_non_receipt_confirmed": False},
        )

    assert response.status_code == 202
    assert invalid.status_code == 422
    service.retry_wms_action.assert_awaited_once_with(
        "run-1",
        client_request_id=payload["client_request_id"],
        wms_non_receipt_confirmed=True,
        expected_version=0,
        actor_id=42,
    )


@pytest.mark.asyncio
async def test_bin_inbound_batch_passes_the_admin_selected_max_count() -> None:
    service = _service()
    payload = {
        "expected_version": 0,
        "client_request_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4474",
        "rack_id": "RACK-01",
        "rack_face": "90",
        "max_bin_count": 1,
    }
    async with AsyncClient(transport=ASGITransport(app=_app(service)), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/workline-integration-debug/runs/run-1/wms/bin-inbound-batch",
            json=payload,
        )

    assert response.status_code == 202
    assert service.send_bin_inbound_batch.await_args.kwargs["max_bin_count"] == 1


@pytest.mark.asyncio
async def test_close_passes_both_explicit_cleanup_confirmations() -> None:
    service = _service()
    async with AsyncClient(transport=ASGITransport(app=_app(service)), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/workline-integration-debug/runs/run-1/close",
            json={
                "expected_version": 1,
                "wms_cleanup_confirmed": True,
                "site_cleanup_confirmed": True,
            },
        )

    assert response.status_code == 200
    assert service.close_run.await_args.kwargs["wms_cleanup_confirmed"] is True
    assert service.close_run.await_args.kwargs["site_cleanup_confirmed"] is True


@pytest.mark.asyncio
async def test_export_gives_wms_csharp_team_exact_operations_and_retry_rules() -> None:
    async with AsyncClient(transport=ASGITransport(app=_app(_service())), base_url="http://test") as client:
        response = await client.get("/api/v1/workline-integration-debug/runs/run-1/export")

    assert response.status_code == 200
    payload = response.json()
    operations = [item["operation"] for item in payload["wms_operations"]]
    assert operations == [
        "outbound.picking_task.issued@v1",
        "outbound.picking_task.prepare@v1",
        "outbound.picking_task.plan_delta@v1",
        "outbound.bin.inbound_batch@v1",
        "outbound.manual_bin.work_admission_decide@v1",
        "outbound.manual_bin.work_completed@v1",
        "outbound.manual_bin.completion_apply_report@v1",
        "outbound.bin.return_batch@v1",
        "outbound.rack.departure_decide@v1",
        "outbound.picking_task.completion_confirm@v1",
    ]
    assert "long" in payload["csharp6_rules"][0]
    assert "frozenJson" in payload["csharp6_httpclient_example"]
