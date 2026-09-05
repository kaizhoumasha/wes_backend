from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.workline.models import WorkLineConfigurationUpdate, WorkLineStateTransitionRequest
from src.app.workline.v1 import workline as workline_api
from src.core.response import ResourceErrorCode


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


def test_configuration_status_route_requires_dedicated_permission() -> None:
    route = next(
        route
        for route in workline_api.router.routes
        if route.path == "/work_lines/{id}/configuration-status" and "GET" in route.methods
    )

    assert [getattr(dep.dependency, "permission_required", "") for dep in route.dependencies] == [
        "biz:workline:configuration-status"
    ]

    available_plugins_route = next(
        route
        for route in workline_api.router.routes
        if route.path == "/work_lines/{id}/available-plugins" and "GET" in route.methods
    )
    assert [getattr(dep.dependency, "permission_required", "") for dep in available_plugins_route.dependencies] == [
        "biz:workline:available-plugins"
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


@pytest.mark.asyncio
async def test_configuration_status_route_converts_missing_workline_to_not_found() -> None:
    service = SimpleNamespace(configuration_status=AsyncMock(side_effect=ValueError("WorkLine 不存在: 404")))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(workline_configuration_service=service)))

    response = await workline_api.get_workline_configuration_status(object(), request=request, id=404)

    assert response["code"] == ResourceErrorCode.NOT_FOUND.code
    assert "不存在" in response["message"]


def test_activate_route_is_removed_because_start_is_the_only_activation_entry() -> None:
    assert not any(route.path == "/work_lines/{id}/activate" for route in workline_api.router.routes)


@pytest.mark.asyncio
async def test_deactivate_route_converts_missing_workline_to_not_found() -> None:
    service = SimpleNamespace(deactivate=AsyncMock(side_effect=ValueError("WorkLine 不存在: 404")))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(workline_configuration_service=service)))
    db = object()
    cache = object()

    response = await workline_api.deactivate_workline(
        db,
        cache,
        request,
        id=404,
        payload=WorkLineStateTransitionRequest(version=0),
    )

    assert response["code"] == ResourceErrorCode.NOT_FOUND.code
    assert "不存在" in response["message"]


@pytest.mark.asyncio
async def test_configuration_route_saves_plugin_config_and_device_set_through_one_service() -> None:
    route = next(
        route
        for route in workline_api.router.routes
        if route.path == "/work_lines/{id}/configuration" and "PUT" in route.methods
    )
    assert [getattr(dep.dependency, "permission_required", "") for dep in route.dependencies] == [
        "biz:workline:configure"
    ]

    workline = SimpleNamespace(id=7, version=4, plugin_key="example_plugin", config={"mode": "AUTO"})
    service = SimpleNamespace(
        save=AsyncMock(return_value=SimpleNamespace(workline=workline, device_codes=("D-1", "D-2")))
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(workline_configuration_service=service)))
    db = object()
    cache = object()
    payload = WorkLineConfigurationUpdate(
        version=3,
        plugin_key="example_plugin",
        config={"mode": "AUTO"},
        device_codes=("D-2", "D-1"),
    )

    response = await workline_api.save_workline_configuration(
        db,
        cache,
        request,
        id=7,
        payload=payload,
    )

    assert response["data"].device_codes == ("D-1", "D-2")
    service.save.assert_awaited_once_with(
        db,
        workline_id=7,
        version=3,
        plugin_key="example_plugin",
        config={"mode": "AUTO"},
        device_codes=("D-2", "D-1"),
        cache=cache,
    )
