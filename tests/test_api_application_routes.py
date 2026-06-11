from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.inspection import inspect as sa_inspect

from src.app.api_auth.models import APIApplication, APIApplicationResponse
from src.app.api_auth.models.api_application import ValidityPeriod
from src.app.api_auth.v1 import api_application as api_application_module


def _get_route(path: str, method: str):
    for route in api_application_module.router.routes:
        if method in route.methods and route.path == path:
            return route
    raise AssertionError(f"{method} {path} route not found")


def _permission_names(path: str, method: str) -> list[str]:
    route = _get_route(path, method)
    return [getattr(dep.dependency, "permission_required", "") for dep in route.dependencies]


def test_available_permissions_routes_split_read_and_sync_permissions() -> None:
    assert _permission_names("/applications/available-permissions", "GET") == [
        "api-auth:api_application:list_permissions"
    ]
    assert _permission_names("/applications/available-permissions/sync", "POST") == [
        "api-auth:api_application:sync_permissions"
    ]


def test_generated_crud_routes_use_api_application_permission_resource() -> None:
    assert _permission_names("/applications/query", "POST") == ["api-auth:api_application:list"]
    assert _permission_names("/applications/{id}", "GET") == ["api-auth:api_application:detail"]
    assert _permission_names("/applications/{id}", "PUT") == ["api-auth:api_application:update"]
    assert _permission_names("/applications/{id}", "DELETE") == ["api-auth:api_application:delete"]
    assert _permission_names("/applications/trash/permanent", "DELETE") == ["api-auth:api_application:permanent_delete"]
    assert _permission_names("/applications/{id}/permanent", "DELETE") == ["api-auth:api_application:permanent_delete"]


def test_workline_inbound_handoff_routes_are_registered_under_workline_router() -> None:
    from src.app.workline.v1 import router as workline_router

    route_paths = {(route.path, tuple(sorted(route.methods))) for route in workline_router.routes}

    assert ("/inbound-handoff/demands", ("GET",)) in route_paths
    assert ("/inbound-handoff/demands/{demand_id}", ("GET",)) in route_paths
    assert ("/inbound-handoff/source-items/{source_item_id}/actions/retry-source-pick", ("POST",)) in route_paths


def test_application_response_exposes_assigned_permissions() -> None:
    assert "permissions" in APIApplicationResponse.model_fields


def test_application_model_registers_permissions_relationship() -> None:
    relationship = sa_inspect(APIApplication).relationships["permissions"]

    assert relationship.secondary is not None
    assert relationship.secondary.name == "api_app_permissions"


@pytest.mark.asyncio
async def test_reset_validity_route_serializes_with_service_to_avoid_lazy_permissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = _get_route("/applications/{id}/reset-validity", "POST")
    app = object()
    serialized_app = {"id": 12, "app_id": "app_lazy", "permissions": []}
    monkeypatch.setattr(api_application_module.api_app_service, "reset_validity_period", AsyncMock(return_value=app))
    monkeypatch.setattr(api_application_module.api_app_service, "get_by_id", AsyncMock(return_value=app))
    to_response = Mock(return_value=serialized_app)
    monkeypatch.setattr(api_application_module.api_app_service, "to_response", to_response)
    model_validate = Mock(side_effect=AssertionError("direct model_validate would access lazy permissions"))
    monkeypatch.setattr(api_application_module.APIApplicationResponse, "model_validate", model_validate)
    db = object()
    cache = object()

    response = await route.endpoint(
        id=12,
        data=api_application_module.ResetValidityPeriodSchema(version=1, validity_period=ValidityPeriod.ONE_YEAR),
        db=db,
        cache=cache,
    )

    api_application_module.api_app_service.get_by_id.assert_awaited_once_with(db, cache, 12, max_depth=1)
    to_response.assert_called_once_with(app, api_application_module.APIApplicationResponse)
    model_validate.assert_not_called()
    assert response["data"] == serialized_app


@pytest.mark.asyncio
async def test_available_permissions_route_returns_only_app_api_permissions(monkeypatch: pytest.MonkeyPatch) -> None:
    route = _get_route("/applications/available-permissions", "GET")
    db = object()
    get_api_permissions = AsyncMock(return_value=[])
    monkeypatch.setattr(api_application_module.permission_service, "get_api_permissions", get_api_permissions)

    response = await route.endpoint(db=db)

    get_api_permissions.assert_awaited_once_with(db, perm_type="app_api", exclude_deleted=True)
    assert response["data"] == []


@pytest.mark.asyncio
async def test_sync_available_permissions_route_returns_only_app_api_permissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = _get_route("/applications/available-permissions/sync", "POST")
    app = object()
    db = object()
    sync_permissions_to_db = AsyncMock(return_value={"created": 0, "updated": 0, "skipped": 0, "total": 0})
    get_api_permissions = AsyncMock(return_value=[])
    monkeypatch.setattr(api_application_module, "sync_permissions_to_db", sync_permissions_to_db)
    monkeypatch.setattr(api_application_module.permission_service, "get_api_permissions", get_api_permissions)

    response = await route.endpoint(request=SimpleNamespace(app=app), db=db)

    sync_permissions_to_db.assert_awaited_once_with(app, db)
    get_api_permissions.assert_awaited_once_with(db, perm_type="app_api", exclude_deleted=True)
    assert response["data"] == []


@pytest.mark.asyncio
async def test_assign_permissions_route_maps_value_error_to_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    route = _get_route("/applications/{id}/permissions", "POST")
    monkeypatch.setattr(
        api_application_module.api_app_service,
        "assign_permissions",
        AsyncMock(side_effect=ValueError("应用 8 不存在")),
    )

    response = await route.endpoint(id=8, permission_ids=[1, 2], db=object(), cache=object())

    assert response["code"] == "3000"
    assert response["message"] == "APIApplication (ID: 8) 不存在"


@pytest.mark.asyncio
async def test_reset_secret_route_maps_value_error_to_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    route = _get_route("/applications/{id}/reset-secret", "POST")
    monkeypatch.setattr(
        api_application_module.api_app_service,
        "reset_secret",
        AsyncMock(side_effect=ValueError("APIApplication 不存在")),
    )

    response = await route.endpoint(id=9, db=object(), cache=object())

    assert response["code"] == "3000"
    assert response["message"] == "APIApplication (ID: 9) 不存在"


@pytest.mark.asyncio
async def test_reset_validity_route_maps_value_error_to_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    route = _get_route("/applications/{id}/reset-validity", "POST")
    monkeypatch.setattr(
        api_application_module.api_app_service,
        "reset_validity_period",
        AsyncMock(side_effect=ValueError("应用 10 不存在")),
    )

    response = await route.endpoint(
        id=10,
        data=api_application_module.ResetValidityPeriodSchema(version=1, validity_period=ValidityPeriod.ONE_YEAR),
        db=object(),
        cache=object(),
    )

    assert response["code"] == "3000"
    assert response["message"] == "APIApplication (ID: 10) 不存在"
