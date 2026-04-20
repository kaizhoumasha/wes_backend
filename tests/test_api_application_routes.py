from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

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
