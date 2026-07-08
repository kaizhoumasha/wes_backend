from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.app.runtime.capabilities.phase4.start_admission_service import StartAdmissionResult
from src.app.workline.services.safety_service import WorkLineSafetyBlocked
from src.app.workline.v1 import operation as operation_api


def test_sandbox_process_route_is_removed() -> None:
    route_paths = {getattr(route, "path", None) for route in operation_api.router.routes}

    assert "/sandbox/process" not in route_paths


class _StartAdmissionServiceStub:
    def __init__(self, result: StartAdmissionResult) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def admit_start(self, _db: Any, workline_id: int, **kwargs: Any) -> StartAdmissionResult:
        self.calls.append({"workline_id": workline_id, **kwargs})
        return self.result


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
            release_evidence_json={"workline_runtime_status": "STOPPED"},
        )


class _DbStub:
    def __init__(self) -> None:
        self.committed = False

    async def commit(self) -> None:
        self.committed = True


def _response_data(response: dict[str, Any]) -> dict[str, Any]:
    data = response["data"]
    return data.model_dump() if hasattr(data, "model_dump") else data


def test_sandbox_workline_start_route_uses_user_update_permission() -> None:
    route = next(
        route
        for route in operation_api.router.routes
        if getattr(route, "path", None) == "/sandbox/worklines/{workline_id}/start"
    )
    permissions = [
        getattr(getattr(dependency, "dependency", None), "permission_required", None)
        for dependency in getattr(route, "dependencies", [])
    ]

    assert permissions == ["biz:workline:update"]


def test_sandbox_workline_start_imports_start_admission_service_instance() -> None:
    # Regression: ISSUE-001 — sandbox START imported the phase4 module,
    # not the service instance.
    # Found by /qa on 2026-07-08
    # Report: .gstack/qa-reports/qa-report-localhost-2026-07-08.md
    assert hasattr(operation_api.start_admission_service, "admit_start")


@pytest.mark.asyncio
async def test_sandbox_workline_start_returns_accepted_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _StartAdmissionServiceStub(
        StartAdmissionResult(
            accepted=True,
            http_status=200,
            reason_code=None,
            message="START 准入通过",
            workline_id=45,
            diagnostic={"checked_devices": ["ARM03"]},
        )
    )
    monkeypatch.setattr(operation_api, "start_admission_service", service)

    response = await operation_api.start_sandbox_workline(
        workline_id=45,
        payload=operation_api.SandboxWorklineStartRequest(
            device_code="ARM03",
            trace_id="sandbox:start:trace-1",
        ),
        db=_DbStub(),  # type: ignore[arg-type]
    )

    data = _response_data(response)
    assert response["code"] == "1000"
    assert data["status"] == "accepted"
    assert data["device_code"] == "ARM03"
    assert data["trace_id"] == "sandbox:start:trace-1"
    assert data["diagnostic"] == {"checked_devices": ["ARM03"]}
    assert service.calls == [
        {
            "workline_id": 45,
            "source_device_code": "ARM03",
            "request_id": "sandbox:start:trace-1",
            "trace_id": "sandbox:start:trace-1",
        }
    ]


@pytest.mark.asyncio
async def test_sandbox_workline_start_returns_rejection_as_success_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _StartAdmissionServiceStub(
        StartAdmissionResult(
            accepted=False,
            http_status=409,
            reason_code="START_ADMISSION_DEVICE_NOT_IDLE",
            message="START 准入失败: 设备 RS-CONV-01 非空闲",
            workline_id=45,
            diagnostic={"device_code": "RS-CONV-01", "status": "RUNNING"},
        )
    )
    monkeypatch.setattr(operation_api, "start_admission_service", service)

    response = await operation_api.start_sandbox_workline(
        workline_id=45,
        payload=operation_api.SandboxWorklineStartRequest(
            device_code="RS-CONV-01",
            trace_id="sandbox:start:trace-rejected",
        ),
        db=_DbStub(),  # type: ignore[arg-type]
    )

    data = _response_data(response)
    assert response["code"] == "1000"
    assert data["ack"] is False
    assert data["reason_code"] == "START_ADMISSION_DEVICE_NOT_IDLE"
    assert data["diagnostic"] == {
        "device_code": "RS-CONV-01",
        "status": "RUNNING",
        "message": "START 准入失败: 设备 RS-CONV-01 非空闲",
        "workline_id": 45,
    }


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
    assert "release_message" not in response["data"]


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
    assert response["data"]["workline_runtime_status"] == "STOPPED"
    assert response["data"]["release_message"] == "已解除冻结，等待现场 START"
