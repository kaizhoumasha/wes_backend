"""HTTP contract for Device-scoped ECS_TEST defaults."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from src.app.device.models.device import DeviceCreate, DeviceResponse, DeviceUpdate
from src.app.device.services.device_service import device_service
from src.core import rbac
from src.core.security import require_auth
from src.database.dependencies import _get_cache_service, get_db
from src.register import register_exception, register_routers

_PATH = "/api/v1/device/devices/SOURCE-1/ecs-test-default"
_ROUTE_PATH = "/api/v1/device/devices/{device_code}/ecs-test-default"
_DEFAULT = {"target_device_code": "TARGET-2", "task_type": "MOVE_FORWARD", "params": {}}


def _device(default: dict[str, object] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=17,
        device_code="SOURCE-1",
        version=4,
        ecs_test_default_json=default,
    )


def _app(monkeypatch: pytest.MonkeyPatch, permissions: set[str]) -> FastAPI:
    app = FastAPI()
    register_exception(app)
    register_routers(app)
    app.dependency_overrides[require_auth] = lambda: 42
    app.dependency_overrides[get_db] = lambda: object()
    app.dependency_overrides[_get_cache_service] = lambda: None

    async def get_permissions(*_args: object) -> set[str]:
        return permissions

    monkeypatch.setattr(rbac, "get_user_permissions", get_permissions)
    return app


def _permission(app: FastAPI, method: str) -> str:
    route = next(
        item
        for item in app.routes
        if isinstance(item, APIRoute) and item.path == _ROUTE_PATH and method in item.methods
    )
    return next(
        dependency.call.permission_required
        for dependency in route.dependant.dependencies
        if hasattr(dependency.call, "permission_required")
    )


@pytest.mark.asyncio
async def test_get_returns_null_default_in_unified_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    device = _device()
    monkeypatch.setattr(device_service, "get_device_by_code", AsyncMock(return_value=device))
    app = _app(monkeypatch, {"biz:device:detail"})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(_PATH)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"code", "message", "data", "timestamp"}
    assert body["code"] == "1000"
    assert body["data"] == {
        "device_id": 17,
        "device_code": "SOURCE-1",
        "device_version": 4,
        "default": None,
    }


@pytest.mark.asyncio
async def test_put_saves_complete_default_and_explicit_null_clears(monkeypatch: pytest.MonkeyPatch) -> None:
    saved = _device(_DEFAULT)
    monkeypatch.setattr(device_service, "save_ecs_test_default", AsyncMock(return_value=saved), raising=False)
    app = _app(monkeypatch, {"biz:device:update"})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(_PATH, json={"default": _DEFAULT})
        assert response.status_code == 200
        assert response.json()["data"] == {
            "device_id": 17,
            "device_code": "SOURCE-1",
            "device_version": 4,
            "default": _DEFAULT,
        }

        await client.put(_PATH, json={"default": None})

    assert device_service.save_ecs_test_default.await_args_list[0].args[1:] == (
        "SOURCE-1",
        _DEFAULT,
        None,
    )
    assert device_service.save_ecs_test_default.await_args_list[1].args[1:] == ("SOURCE-1", None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"default": {"target_device_code": "TARGET-2", "task_type": "MOVE_FORWARD"}},
        {"default": {**_DEFAULT, "source_device_code": "SOURCE-1"}},
        {"default": {**_DEFAULT, "unexpected": True}},
        {"default": None, "unexpected": True},
    ],
)
async def test_put_rejects_implicit_clear_missing_params_and_extra_fields(
    payload: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(device_service, "save_ecs_test_default", AsyncMock(), raising=False)
    app = _app(monkeypatch, {"biz:device:update"})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(_PATH, json=payload)

    assert response.status_code == 422
    device_service.save_ecs_test_default.assert_not_awaited()


def test_generic_device_crud_schemas_do_not_expose_ecs_test_default() -> None:
    for schema in (DeviceCreate, DeviceUpdate, DeviceResponse):
        assert "ecs_test_default_json" not in schema.model_fields


@pytest.mark.asyncio
async def test_get_and_put_use_device_detail_and_update_permissions(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _permission(_app(monkeypatch, set()), "GET") == "biz:device:detail"
    assert _permission(_app(monkeypatch, set()), "PUT") == "biz:device:update"

    monkeypatch.setattr(device_service, "get_device_by_code", AsyncMock(return_value=_device()))
    monkeypatch.setattr(
        device_service, "save_ecs_test_default", AsyncMock(return_value=_device(_DEFAULT)), raising=False
    )

    detail_app = _app(monkeypatch, {"biz:device:detail"})
    async with AsyncClient(transport=ASGITransport(app=detail_app), base_url="http://test") as client:
        assert (await client.get(_PATH)).status_code == 200
        assert (await client.put(_PATH, json={"default": _DEFAULT})).status_code == 403

    update_app = _app(monkeypatch, {"biz:device:update"})
    async with AsyncClient(transport=ASGITransport(app=update_app), base_url="http://test") as client:
        assert (await client.get(_PATH)).status_code == 403
        assert (await client.put(_PATH, json={"default": _DEFAULT})).status_code == 200

    superuser_app = _app(monkeypatch, {rbac.SUPERUSER_PERMISSION})
    async with AsyncClient(transport=ASGITransport(app=superuser_app), base_url="http://test") as client:
        assert (await client.get(_PATH)).status_code == 200
        assert (await client.put(_PATH, json={"default": _DEFAULT})).status_code == 200


@pytest.mark.asyncio
async def test_missing_device_returns_not_found_for_get_and_put(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(device_service, "get_device_by_code", AsyncMock(return_value=None))
    monkeypatch.setattr(device_service, "save_ecs_test_default", AsyncMock(return_value=None), raising=False)
    app = _app(monkeypatch, {"biz:device:detail", "biz:device:update"})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        get_response = await client.get(_PATH)
        put_response = await client.put(_PATH, json={"default": _DEFAULT})

    assert get_response.status_code == 404
    assert put_response.status_code == 404
