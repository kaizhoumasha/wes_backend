from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.workline.models import (
    WorkLineActivationRequest,
    WorkLineStateTransitionRequest,
)
from src.app.workline.v1 import workline as workline_api
from src.core.response import ResourceErrorCode


def test_activation_reason_is_not_exposed_by_deactivation_request_contract() -> None:
    activation_request_model = getattr(workline_api, "WorkLineActivationRequest", None)

    assert activation_request_model is not None
    activation = activation_request_model(version=7, reason="  change-window-42  ")
    deactivation = WorkLineStateTransitionRequest(version=7)

    assert activation.reason == "change-window-42"
    assert not hasattr(deactivation, "reason")


def test_plane_routes_require_dedicated_permissions() -> None:
    """plane scene/snapshot 使用独立权限, 不能复用普通 detail。"""

    from src.app.workline.services.plane_service import plane_read_security_policy

    scene_route = next(
        route
        for route in workline_api.router.routes
        if route.path == "/work_lines/{id}/plane/scene" and "GET" in route.methods
    )
    snapshot_route = next(
        route
        for route in workline_api.router.routes
        if route.path == "/work_lines/{id}/plane/snapshot" and "GET" in route.methods
    )

    assert [getattr(dep.dependency, "permission_required", "") for dep in scene_route.dependencies] == [
        plane_read_security_policy.scene_permission
    ]
    assert [getattr(dep.dependency, "permission_required", "") for dep in snapshot_route.dependencies] == [
        plane_read_security_policy.snapshot_permission
    ]


@pytest.mark.asyncio
async def test_plane_scene_route_records_read_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    """plane scene route 返回成功时必须记录读取审计。"""

    from src.app.workline.models import PlaneSceneView
    from src.app.workline.services.plane_service import PlaneReadPrincipal

    service = SimpleNamespace(
        get_scene=AsyncMock(
            return_value=PlaneSceneView(
                schema_version="plane.scene.v1",
                workline_code="WL-7",
                nodes=[],
                edges=[],
            )
        ),
        record_read_audit=AsyncMock(),
    )
    monkeypatch.setattr(workline_api, "workline_plane_service", service)
    db = SimpleNamespace()
    cache = SimpleNamespace()
    principal = PlaneReadPrincipal(user_id=42, is_superuser=False)

    await workline_api.get_workline_plane_scene(db=db, cache=cache, id=7, principal=principal)

    service.get_scene.assert_awaited_once_with(db, cache, 7, principal=principal)
    service.record_read_audit.assert_awaited_once_with(db, view="scene", workline_id=7, workline_code="WL-7")


@pytest.mark.asyncio
async def test_plane_snapshot_route_records_read_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    """plane snapshot route 返回成功时必须记录读取审计。"""

    from src.app.workline.models import PlaneSnapshot
    from src.app.workline.services.plane_service import PlaneReadPrincipal

    service = SimpleNamespace(
        get_snapshot=AsyncMock(
            return_value=PlaneSnapshot(
                schema_version="plane.snapshot.v1",
                workline_code="WL-7",
                scene_schema_version="plane.scene.v1",
                objects=[],
                extremes=[],
            )
        ),
        record_read_audit=AsyncMock(),
    )
    monkeypatch.setattr(workline_api, "workline_plane_service", service)
    db = SimpleNamespace()
    cache = SimpleNamespace()
    principal = PlaneReadPrincipal(user_id=42, is_superuser=False)

    await workline_api.get_workline_plane_snapshot(db=db, cache=cache, id=7, principal=principal)

    service.get_snapshot.assert_awaited_once_with(db, cache, 7, principal=principal)
    service.record_read_audit.assert_awaited_once_with(db, view="snapshot", workline_id=7, workline_code="WL-7")


def test_plugin_definition_route_accepts_encoded_slash_plugin_keys() -> None:
    route = next(
        route
        for route in workline_api.router.routes
        if route.path == "/plugins/{plugin_key:path}/manifest" and "GET" in route.methods
    )

    match, child_scope = route.matches(
        {
            "type": "http",
            "method": "GET",
            "path": "/plugins/plugin%20sample%2F1/manifest",
            "root_path": "",
        }
    )

    assert match.name == "FULL"
    assert child_scope["path_params"]["plugin_key"] == "plugin%20sample%2F1"


@pytest.mark.asyncio
async def test_get_plugin_definition_decodes_encoded_plugin_key(monkeypatch) -> None:
    seen_plugin_keys: list[str] = []
    summary = object()

    def get_plugin_definition_summary(
        plugin_key: str,
        contract_version: str | None = None,
    ) -> object:
        seen_plugin_keys.append(plugin_key)
        return summary

    service = SimpleNamespace(get_plugin_definition_summary=get_plugin_definition_summary)
    monkeypatch.setattr(workline_api, "workline_service", service)

    response = await workline_api.get_workline_plugin_definition("plugin%20sample%2F1")

    assert response["code"] == "1000"
    assert response["data"] == summary
    assert seen_plugin_keys == ["plugin sample/1"]


@pytest.mark.asyncio
async def test_configuration_status_route_converts_missing_workline_to_not_found(monkeypatch) -> None:
    service = SimpleNamespace(configuration_status=AsyncMock(side_effect=ValueError("WorkLine 不存在: 404")))
    monkeypatch.setattr(workline_api, "workline_service", service)

    response = await workline_api.get_workline_configuration_status(object(), id=404)

    assert response["code"] == ResourceErrorCode.NOT_FOUND.code
    assert "不存在" in response["message"]


@pytest.mark.asyncio
async def test_activate_route_converts_missing_workline_to_not_found(monkeypatch) -> None:
    service = SimpleNamespace(activate=AsyncMock(side_effect=ValueError("WorkLine 不存在: 404")))
    monkeypatch.setattr(workline_api, "workline_service", service)
    db = object()
    cache = object()

    response = await workline_api.activate_workline(
        db,
        cache,
        current_user_id=7,
        id=404,
        payload=WorkLineActivationRequest(version=0),
    )

    assert response["code"] == ResourceErrorCode.NOT_FOUND.code
    assert "不存在" in response["message"]
    service.activate.assert_awaited_once_with(
        db,
        404,
        version=0,
        cache=cache,
        actor="7",
        reason="人工启用作业线（用户 7）",
    )


@pytest.mark.asyncio
async def test_activate_route_passes_authenticated_actor_and_requested_reason(monkeypatch) -> None:
    service = SimpleNamespace(activate=AsyncMock(side_effect=ValueError("WorkLine 不存在: 404")))
    monkeypatch.setattr(workline_api, "workline_service", service)
    db = object()
    cache = object()

    await workline_api.activate_workline(
        db,
        cache,
        current_user_id=99,
        id=404,
        payload=WorkLineActivationRequest(version=7, reason="  change-window-42  "),
    )

    service.activate.assert_awaited_once_with(
        db,
        404,
        version=7,
        cache=cache,
        actor="99",
        reason="change-window-42",
    )


@pytest.mark.asyncio
async def test_deactivate_route_converts_missing_workline_to_not_found(monkeypatch) -> None:
    service = SimpleNamespace(deactivate=AsyncMock(side_effect=ValueError("WorkLine 不存在: 404")))
    monkeypatch.setattr(workline_api, "workline_service", service)

    response = await workline_api.deactivate_workline(
        object(),
        object(),
        id=404,
        payload=WorkLineStateTransitionRequest(version=0),
    )

    assert response["code"] == ResourceErrorCode.NOT_FOUND.code
    assert "不存在" in response["message"]
