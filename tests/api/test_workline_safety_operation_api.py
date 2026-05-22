from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.app.workline.models.operation import SandboxCleanupResponse
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


class _SandboxCleanupServiceStub:
    def __init__(self) -> None:
        self.preview_workline_id: int | None = None
        self.cleanup_args: dict[str, Any] | None = None

    async def preview_cleanup(self, _db: Any, *, workline_id: int) -> SandboxCleanupResponse:
        self.preview_workline_id = workline_id
        return SandboxCleanupResponse(
            workline_id=workline_id,
            dry_run=True,
            deleted=False,
            counts={"sessions": 1},
            affected_session_ids=[91],
            message="dry-run only",
        )

    async def cleanup_workline(
        self,
        _db: Any,
        *,
        workline_id: int,
        confirmation: str | None,
    ) -> SandboxCleanupResponse:
        self.cleanup_args = {"workline_id": workline_id, "confirmation": confirmation}
        return SandboxCleanupResponse(
            workline_id=workline_id,
            dry_run=False,
            deleted=True,
            counts={"sessions": 1},
            affected_session_ids=[91],
            message="deleted",
        )


class _SandboxCleanupValueErrorStub:
    async def preview_cleanup(self, *_args: Any, **_kwargs: Any) -> SandboxCleanupResponse:
        raise ValueError("仅允许 SIMULATION 工作线执行沙箱清理")

    async def cleanup_workline(self, *_args: Any, **_kwargs: Any) -> SandboxCleanupResponse:
        raise ValueError("仅允许 SIMULATION 工作线执行沙箱清理")


class _DbStub:
    def __init__(self) -> None:
        self.committed = False

    async def commit(self) -> None:
        self.committed = True


def _response_data(response: dict[str, Any]) -> dict[str, Any]:
    data = response["data"]
    return data.model_dump() if hasattr(data, "model_dump") else data


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


def test_cleanup_sandbox_workline_route_uses_dedicated_permission() -> None:
    route = next(
        route
        for route in operation_api.router.routes
        if getattr(route, "path", None) == "/sandbox/worklines/{workline_id}/cleanup"
    )
    permissions = [
        getattr(getattr(dependency, "dependency", None), "permission_required", None)
        for dependency in getattr(route, "dependencies", [])
    ]

    assert "biz:workline:cleanup-sandbox" in permissions
    assert "biz:workline:update" not in permissions


@pytest.mark.asyncio
async def test_cleanup_sandbox_workline_dry_run_previews_without_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _SandboxCleanupServiceStub()
    monkeypatch.setattr(operation_api, "sandbox_cleanup_service", service)
    db = _DbStub()

    response = await operation_api.cleanup_sandbox_workline(
        workline_id=45,
        payload=operation_api.SandboxCleanupRequest(dry_run=True),
        db=db,  # type: ignore[arg-type]
    )

    data = _response_data(response)
    assert response["code"] == "1000"
    assert service.preview_workline_id == 45
    assert service.cleanup_args is None
    assert db.committed is False
    assert data["deleted"] is False


@pytest.mark.asyncio
async def test_cleanup_sandbox_workline_executes_commit_and_publishes_sse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _SandboxCleanupServiceStub()
    publish = AsyncMock()
    monkeypatch.setattr(operation_api, "sandbox_cleanup_service", service)
    monkeypatch.setattr(operation_api, "publish_deferred_sse_events", publish)
    db = _DbStub()

    response = await operation_api.cleanup_sandbox_workline(
        workline_id=45,
        payload=operation_api.SandboxCleanupRequest(dry_run=False, confirmation="WL-SIM"),
        db=db,  # type: ignore[arg-type]
    )

    data = _response_data(response)
    assert response["code"] == "1000"
    assert service.preview_workline_id is None
    assert service.cleanup_args == {"workline_id": 45, "confirmation": "WL-SIM"}
    assert db.committed is True
    publish.assert_awaited_once_with(db)
    assert data["deleted"] is True


@pytest.mark.asyncio
async def test_cleanup_sandbox_workline_maps_value_error_without_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(operation_api, "sandbox_cleanup_service", _SandboxCleanupValueErrorStub())
    db = _DbStub()

    response = await operation_api.cleanup_sandbox_workline(
        workline_id=45,
        payload=operation_api.SandboxCleanupRequest(dry_run=True),
        db=db,  # type: ignore[arg-type]
    )

    assert db.committed is False
    assert response["code"] == "4001"
    assert "SIMULATION" in response["message"]


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
