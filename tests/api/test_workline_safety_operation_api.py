from types import SimpleNamespace
from typing import Any

import pytest

from src.app.workline.services.safety_service import WorkLineSafetyBlocked
from src.app.workline.v1 import operation as operation_api


def test_sandbox_process_route_is_removed() -> None:
    route_paths = {getattr(route, "path", None) for route in operation_api.router.routes}

    assert "/sandbox/process" not in route_paths


class _SafetyServiceStub:
    async def simulate_estop(self, *_args: Any, **kwargs: Any) -> object:
        return SimpleNamespace(
            id=77,
            workline_id=kwargs["workline_id"],
            status="ACTIVE",
            event_type="ESTOP_PRESSED",
            reason=kwargs["reason"],
            drain_status="COMPLETED",
            evidence_json={"sessions_failed": 0},
            recovery_check_json={},
            cleared_at=None,
            cleared_by=None,
        )


class _SafetyServiceBlockedStub:
    async def simulate_estop(self, *_args: Any, **_kwargs: Any) -> object:
        raise WorkLineSafetyBlocked("WORKLINE_NOT_FOUND: workline_id=404")


class _SafetyServiceClearStub:
    def __init__(self) -> None:
        self.operator_id: int | None = None

    async def clear_estop(self, *_args: Any, **kwargs: Any) -> object:
        self.operator_id = kwargs["operator_id"]
        return SimpleNamespace(
            id=78,
            workline_id=kwargs["workline_id"],
            status="CLEARED",
            event_type="ESTOP_PRESSED",
            reason="ESTOP_PRESSED",
            drain_status="COMPLETED",
            evidence_json={},
            recovery_check_json=kwargs["checks"],
            cleared_at=None,
            cleared_by=kwargs["operator_id"],
        )


class _DbStub:
    def __init__(self) -> None:
        self.committed = False

    async def commit(self) -> None:
        self.committed = True


@pytest.mark.asyncio
async def test_simulate_workline_estop_returns_incident_and_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(operation_api, "workline_safety_service", _SafetyServiceStub())
    db = _DbStub()

    response = await operation_api.simulate_workline_estop(
        workline_id=12,
        payload=operation_api.SimulateWorkLineEstopRequest(
            reason="沙箱验证",
            payload={"operator": "qa"},
        ),
        db=db,  # type: ignore[arg-type]
    )

    assert db.committed is True
    assert response["code"] == "1000"
    assert response["data"]["workline_id"] == 12
    assert response["data"]["event_type"] == "ESTOP_PRESSED"
    assert response["data"]["reason"] == "沙箱验证"


@pytest.mark.asyncio
async def test_simulate_workline_estop_maps_safety_blocked_to_operation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(operation_api, "workline_safety_service", _SafetyServiceBlockedStub())
    db = _DbStub()

    response = await operation_api.simulate_workline_estop(
        workline_id=404,
        payload=operation_api.SimulateWorkLineEstopRequest(reason="沙箱验证"),
        db=db,  # type: ignore[arg-type]
    )

    assert db.committed is False
    assert response["code"] == "3000"
    assert "WORKLINE_NOT_FOUND" in response["message"]


def test_clear_estop_route_uses_dedicated_permission() -> None:
    route = next(
        route
        for route in operation_api.router.routes
        if getattr(route, "path", None) == "/safety/worklines/{workline_id}/clear-estop"
    )
    permissions = [
        getattr(getattr(dependency, "dependency", None), "permission_required", None)
        for dependency in getattr(route, "dependencies", [])
    ]

    assert "biz:workline:clear-estop" in permissions
    assert "biz:workline:update" not in permissions


def test_resolve_runtime_reconciliation_route_uses_dedicated_permission() -> None:
    route = next(
        route
        for route in operation_api.router.routes
        if getattr(route, "path", None) == "/reconciliations/sessions/{session_id}/resolve"
    )
    permissions = [
        getattr(getattr(dependency, "dependency", None), "permission_required", None)
        for dependency in getattr(route, "dependencies", [])
    ]

    assert "biz:workline:resolve-reconciliation" in permissions
    assert "biz:workline:update" not in permissions


def test_clear_estop_request_does_not_accept_operator_id() -> None:
    assert "operator_id" not in operation_api.ClearWorkLineEstopRequest.model_fields


@pytest.mark.asyncio
async def test_clear_workline_estop_uses_authenticated_user_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _SafetyServiceClearStub()
    monkeypatch.setattr(operation_api, "workline_safety_service", service)
    db = _DbStub()

    response = await operation_api.clear_workline_estop(
        workline_id=12,
        payload=operation_api.ClearWorkLineEstopRequest(
            checks={"estop_button_reset": True, "area_safe": True},
            reason="现场确认安全",
        ),
        db=db,  # type: ignore[arg-type]
        current_user_id=88,
    )

    assert db.committed is True
    assert response["code"] == "1000"
    assert service.operator_id == 88
    assert response["data"]["cleared_by"] == 88
