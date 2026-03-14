from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field

from src.core.base_api import BaseAPI


class DummyModel:
    pass


class DummySoftDeleteModel:
    is_deleted = True

    def soft_delete(self, deleted_by: int | None = None) -> None:
        return None

    def restore(self) -> None:
        return None


class ChildResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class DummyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    children: list[ChildResponse] = Field(default_factory=list)


class DummyUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class FakeService:
    def __init__(self) -> None:
        self.update_calls: list[tuple[object, int, dict[str, Any], object]] = []
        self.get_by_id_calls: list[tuple[object, object, int, int, bool]] = []

    async def update(self, db: object, id: int, data: dict[str, Any], cache: object) -> SimpleNamespace:
        self.update_calls.append((db, id, dict(data), cache))
        return SimpleNamespace(id=id, name=data["name"], children=[])

    async def get_by_id(
        self,
        db: object,
        cache: object,
        id: int,
        max_depth: int = 2,
        include_deleted: bool = False,
    ) -> SimpleNamespace:
        self.get_by_id_calls.append((db, cache, id, max_depth, include_deleted))
        return SimpleNamespace(
            id=id,
            name="reloaded",
            children=[SimpleNamespace(id=10, name="child")],
        )

    def to_response(self, resource: object, schema: type[BaseModel]) -> BaseModel:
        return schema.model_validate(resource)


def _get_update_endpoint(api: BaseAPI[Any, Any, Any]):
    for route in api.router.routes:
        if "PUT" in route.methods and route.path == "/dummy/{id}":
            return route.endpoint
    raise AssertionError("update endpoint not found")


@pytest.mark.asyncio
async def test_update_response_reload_includes_relations() -> None:
    service = FakeService()
    api = BaseAPI(
        module_name="test",
        model=DummyModel,
        service=service,
        update_schema=DummyUpdate,
        response_schema=DummyResponse,
        prefix="/dummy",
        gen_create=False,
        gen_delete=False,
        enable_permission=False,
    )

    endpoint = _get_update_endpoint(api)
    db = object()
    cache = object()

    response = await endpoint(
        id=1,
        obj_in=DummyUpdate(name="changed", description=None),
        db=db,
        cache=cache,
    )

    assert service.update_calls == [(db, 1, {"name": "changed"}, cache)]
    assert service.get_by_id_calls == [(db, cache, 1, 1, False)]
    assert response["data"].name == "reloaded"
    assert response["data"].children[0].id == 10


def _get_get_endpoint(api: BaseAPI[Any, Any, Any]):
    for route in api.router.routes:
        if "GET" in route.methods and route.path == "/dummy/{id}":
            return route.endpoint
    raise AssertionError("get endpoint not found")


@pytest.mark.asyncio
async def test_get_endpoint_forwards_include_deleted_for_soft_delete_models() -> None:
    service = FakeService()
    api = BaseAPI(
        module_name="test",
        model=DummySoftDeleteModel,
        service=service,
        response_schema=DummyResponse,
        prefix="/dummy",
        gen_create=False,
        gen_update=False,
        gen_delete=False,
        enable_permission=False,
    )

    endpoint = _get_get_endpoint(api)
    db = object()
    cache = object()

    response = await endpoint(
        id=5,
        db=db,
        cache=cache,
        max_depth=3,
        include_deleted=True,
    )

    assert service.get_by_id_calls == [(db, cache, 5, 3, True)]
    assert response["data"].id == 5
