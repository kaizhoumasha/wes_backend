from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

from src.app.workline.models import (
    WorkLineBaseConfigurationResponse,
    WorkLineBaseConfigurationUpdate,
    WorkLineConfigurationUpdate,
    WorkLineStateTransitionRequest,
)
from src.app.workline.v1 import active_objects as active_objects_api
from src.app.workline.v1 import workline as workline_api
from src.core.openapi import generate_route_operation_id
from src.core.response import ResourceErrorCode, ServerErrorCode
from src.utils.permission_scanner import build_validated_permission_leaves


def _route_permission_names(route: object) -> list[str]:
    return [
        permission
        for dependency in route.dependant.dependencies
        if (permission := getattr(dependency.call, "permission_required", ""))
    ]


def test_plane_v2_openapi_publishes_routes_without_internal_device_codes() -> None:
    """OpenAPI 必须可供前端生成 v2 客户端，且不泄露 v1 内部关联字段。"""

    app = FastAPI(generate_unique_id_function=generate_route_operation_id)
    app.include_router(workline_api.router)
    app.include_router(active_objects_api.router)

    schema = app.openapi()

    assert "/work_lines/{id}/plane/scene/v2" in schema["paths"]
    assert "/work_lines/{id}/plane/snapshot/v2" in schema["paths"]
    assert "/work_lines/{id}/active-objects/v2" in schema["paths"]
    assert "device_codes" not in schema["components"]["schemas"]["WorklineActiveObjectView"]["properties"]
    operation_ids = [operation["operationId"] for path in schema["paths"].values() for operation in path.values()]
    assert len(operation_ids) == len(set(operation_ids))
    assert schema["paths"]["/work_lines/{id}/plane/scene/v2"]["get"]["operationId"] == (
        "work_lines_by_id_plane_scene_v2_get"
    )
    assert schema["paths"]["/work_lines/{id}/plane/snapshot/v2"]["get"]["operationId"] == (
        "work_lines_by_id_plane_snapshot_v2_get"
    )
    assert schema["paths"]["/work_lines/{id}/active-objects/v2"]["get"]["operationId"] == (
        "work_lines_by_id_active_objects_v2_get"
    )

    permission_names = [permission["name"] for permission in build_validated_permission_leaves(app)]
    assert permission_names.count("biz:workline:view-plane-scene") == 1
    assert permission_names.count("biz:workline:view-plane-snapshot") == 1
    assert permission_names.count("biz:workline:active-objects") == 1


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
    scene_v2_route = next(
        route
        for route in workline_api.router.routes
        if route.path == "/work_lines/{id}/plane/scene/v2" and "GET" in route.methods
    )
    snapshot_v2_route = next(
        route
        for route in workline_api.router.routes
        if route.path == "/work_lines/{id}/plane/snapshot/v2" and "GET" in route.methods
    )
    active_objects_v2_route = next(
        route
        for route in active_objects_api.router.routes
        if route.path == "/work_lines/{id}/active-objects/v2" and "GET" in route.methods
    )

    assert _route_permission_names(scene_v2_route) == [plane_read_security_policy.scene_permission]
    assert _route_permission_names(snapshot_v2_route) == [plane_read_security_policy.snapshot_permission]
    assert _route_permission_names(active_objects_v2_route) == ["biz:workline:active-objects"]


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
    base_route = next(
        route
        for route in workline_api.router.routes
        if route.path == "/work_lines/{id}/base-configuration" and "GET" in route.methods
    )
    assert [getattr(dep.dependency, "permission_required", "") for dep in base_route.dependencies] == [
        "biz:workline:base-configuration"
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
@pytest.mark.parametrize("view", ["scene", "snapshot"])
async def test_plane_v2_routes_delegate_with_frozen_plugins_and_record_audit(
    monkeypatch: pytest.MonkeyPatch,
    view: str,
) -> None:
    """v2 route 必须使用部署冻结插件集合，并沿用 plane 读取审计。"""

    plugins = (object(),)
    result = SimpleNamespace(workline=SimpleNamespace(line_code="WL-7")) if view == "scene" else SimpleNamespace()
    method = AsyncMock(return_value=result)
    service = SimpleNamespace(**{f"get_{view}_v2": method}, record_read_audit=AsyncMock())
    monkeypatch.setattr(workline_api, "workline_plane_service", service)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(deployment_runtime=SimpleNamespace(plugins=plugins)))
    )
    db, cache = object(), object()
    principal = SimpleNamespace(user_id=42, is_superuser=False)
    handler = (
        workline_api.get_workline_plane_scene_v2 if view == "scene" else workline_api.get_workline_plane_snapshot_v2
    )

    response = await handler(request=request, db=db, cache=cache, principal=principal, _permission=None, id=7)

    assert response["data"] is result
    method.assert_awaited_once_with(db, cache, 7, principal=principal, plugins=plugins)
    service.record_read_audit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("view", ["scene", "snapshot", "active_objects"])
async def test_v2_routes_report_unavailable_deployment_runtime(view: str) -> None:
    """deployment runtime 未就绪时不得调用 service 或猜测插件集合。"""

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    if view == "active_objects":
        response = await active_objects_api.get_workline_active_objects_v2(
            request=request,
            db=object(),
            cache=object(),
            _permission=None,
            id=7,
        )
    else:
        handler = (
            workline_api.get_workline_plane_scene_v2 if view == "scene" else workline_api.get_workline_plane_snapshot_v2
        )
        response = await handler(
            request=request,
            db=object(),
            cache=object(),
            principal=SimpleNamespace(user_id=42, is_superuser=False),
            _permission=None,
            id=7,
        )

    assert response["code"] == ServerErrorCode.SERVICE_UNAVAILABLE.code


@pytest.mark.asyncio
@pytest.mark.parametrize("view", ["scene", "snapshot"])
async def test_plane_v2_routes_map_missing_workline_to_not_found(
    monkeypatch: pytest.MonkeyPatch,
    view: str,
) -> None:
    """v2 plane service 的资源不存在错误必须保持 API NOT_FOUND 语义。"""

    plugins = (object(),)
    method = AsyncMock(side_effect=ValueError("作业线不存在: 404"))
    service = SimpleNamespace(**{f"get_{view}_v2": method}, record_read_audit=AsyncMock())
    monkeypatch.setattr(workline_api, "workline_plane_service", service)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(deployment_runtime=SimpleNamespace(plugins=plugins)))
    )
    handler = (
        workline_api.get_workline_plane_scene_v2 if view == "scene" else workline_api.get_workline_plane_snapshot_v2
    )

    response = await handler(
        request=request,
        db=object(),
        cache=object(),
        principal=SimpleNamespace(user_id=42, is_superuser=False),
        _permission=None,
        id=404,
    )

    assert response["code"] == ResourceErrorCode.NOT_FOUND.code
    service.record_read_audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_active_objects_v2_route_maps_missing_workline_and_returns_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Active Objects v2 route 区分资源不存在与成功响应。"""

    plugins = (object(),)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(deployment_runtime=SimpleNamespace(plugins=plugins)))
    )
    service = SimpleNamespace(get_active_objects_v2=AsyncMock(side_effect=ValueError("作业线不存在: 404")))
    monkeypatch.setattr(active_objects_api, "workline_plane_service", service)
    db, cache = object(), object()

    missing = await active_objects_api.get_workline_active_objects_v2(
        request=request,
        db=db,
        cache=cache,
        _permission=None,
        id=404,
    )

    assert missing["code"] == ResourceErrorCode.NOT_FOUND.code

    result = SimpleNamespace(workline_id=7)
    service.get_active_objects_v2 = AsyncMock(return_value=result)
    success = await active_objects_api.get_workline_active_objects_v2(
        request=request,
        db=db,
        cache=cache,
        _permission=None,
        id=7,
    )

    assert success["data"] is result
    service.get_active_objects_v2.assert_awaited_once_with(db, cache, 7, plugins=plugins)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["configuration_status", "base_configuration"])
async def test_configuration_status_route_converts_missing_workline_to_not_found(method: str) -> None:
    service = SimpleNamespace(**{method: AsyncMock(side_effect=ValueError("WorkLine 不存在: 404"))})
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(workline_configuration_service=service)))

    handler = (
        workline_api.get_workline_base_configuration
        if method == "base_configuration"
        else workline_api.get_workline_configuration_status
    )
    response = await handler(object(), request=request, id=404)

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
@pytest.mark.parametrize("kind", ["plugin", "base"])
async def test_configuration_routes_keep_physical_and_plugin_payloads_separate(kind: str) -> None:
    is_base = kind == "base"
    path = "/work_lines/{id}/base-configuration" if is_base else "/work_lines/{id}/configuration"
    permission = "biz:workline:configure-base" if is_base else "biz:workline:configure"
    route = next(route for route in workline_api.router.routes if route.path == path and "PUT" in route.methods)
    assert [getattr(dep.dependency, "permission_required", "") for dep in route.dependencies] == [permission]
    fields = (
        {"device_codes": ("D-2", "D-1"), "positions": ()}
        if is_base
        else {"plugin_key": "example_plugin", "config": {"mode": "AUTO"}}
    )
    result = (
        WorkLineBaseConfigurationResponse(workline_id=7, version=4, is_active=False, **fields)
        if is_base
        else SimpleNamespace(id=7, version=4, **fields)
    )
    save = AsyncMock(return_value=result)
    service = SimpleNamespace(**{"save_base" if is_base else "save": save})
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(workline_configuration_service=service)))
    db, cache = object(), object()
    if is_base:
        response = await workline_api.save_workline_base_configuration(
            db,
            cache,
            request,
            id=7,
            payload=WorkLineBaseConfigurationUpdate(version=3, **fields),
        )
    else:
        response = await workline_api.save_workline_configuration(
            db,
            cache,
            request,
            id=7,
            payload=WorkLineConfigurationUpdate(version=3, **fields),
        )
    assert response["data"].version == 4
    save.assert_awaited_once_with(db, workline_id=7, version=3, cache=cache, **fields)
