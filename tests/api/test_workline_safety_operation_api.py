from types import SimpleNamespace
from typing import Any

import pytest

from src.app.workline.v1 import operation as operation_api


def test_sandbox_process_route_is_removed() -> None:
    route_paths = {getattr(route, "path", None) for route in operation_api.router.routes}

    assert "/sandbox/process" not in route_paths


def test_target_safety_api_excludes_generic_runtime_control_routes() -> None:
    """Safety 只保留 incident/clear 行为，不暴露 sandbox、replay 或 generic reconciliation。"""

    route_paths = {getattr(route, "path", None) for route in operation_api.router.routes}

    assert route_paths.isdisjoint(
        {
            "/reconciliations/effects/{dispatch_key}/resolve",
            "/reconciliations/sessions/{session_id}/resolve",
            "/replay/inboxes/{inbox_id}",
            "/sandbox/ack",
            "/sandbox/completed",
            "/sandbox/external-callbacks",
            "/sandbox/pending",
            "/sandbox/worklines/{workline_id}/simulate-estop",
        }
    )


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
