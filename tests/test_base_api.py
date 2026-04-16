from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field

from src.core.base_api import BaseAPI
from src.core.openapi import generate_route_operation_id
from src.core.tree_api import BatchSortRequest, SortItem, TreeAPI


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


class DummyCreate(BaseModel):
    name: str


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


class FakeBatchService(FakeService):
    def __init__(self) -> None:
        super().__init__()
        self.create_error: Exception | None = None
        self.delete_results: dict[int, bool] = {}
        self.delete_errors: dict[int, Exception] = {}
        self.restore_results: dict[int, object] = {}
        self.restore_errors: dict[int, Exception] = {}
        self.permanent_delete_results: dict[int, bool] = {}

    async def create(self, db: object, data: dict[str, Any], cache: object) -> object:
        if self.create_error is not None:
            raise self.create_error
        return SimpleNamespace(id=1, name=data["name"], children=[])

    async def delete(self, db: object, id: int, cache: object) -> bool:
        if id in self.delete_errors:
            raise self.delete_errors[id]
        return self.delete_results.get(id, True)

    async def restore(self, db: object, id: int, cache: object) -> object:
        if id in self.restore_errors:
            raise self.restore_errors[id]
        return self.restore_results.get(id, SimpleNamespace(id=id, name=f"restored-{id}", children=[]))

    async def permanent_delete(self, db: object, id: int, cache: object) -> bool:
        return self.permanent_delete_results.get(id, True)


class FakeTreeService(FakeService):
    def __init__(self) -> None:
        super().__init__()
        self.move_error: Exception | None = None
        self.batch_sort_error: Exception | None = None

    async def get_tree(
        self,
        db: object,
        root_id: int | None = None,
        max_depth: int = 1,
        tree_depth: int = 0,
        schema: type[Any] | None = None,
        cache: object | None = None,
    ) -> list[SimpleNamespace]:
        return [SimpleNamespace(id=1, name="root", children=[])]

    async def get_siblings(self, db: object, node_id: int, include_self: bool = False) -> list[SimpleNamespace]:
        return [SimpleNamespace(id=node_id, name="sibling", children=[])]

    async def get_ancestors(self, db: object, node_id: int, include_self: bool = False) -> list[SimpleNamespace]:
        return [SimpleNamespace(id=node_id, name="ancestor", children=[])]

    async def get_children(self, db: object, node_id: int) -> list[SimpleNamespace]:
        return [SimpleNamespace(id=node_id, name="child", children=[])]

    async def move_node(
        self, db: object, node_id: int, new_parent_id: int | None, cache: object | None = None
    ) -> SimpleNamespace:
        if self.move_error is not None:
            raise self.move_error
        return SimpleNamespace(id=node_id, name="moved", children=[])

    async def batch_sort(self, db: object, items: list[dict[str, Any]], cache: object | None = None) -> None:
        if self.batch_sort_error is not None:
            raise self.batch_sort_error


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

    assert service.update_calls == [(db, 1, {"name": "changed", "description": None}, cache)]
    assert service.get_by_id_calls == [(db, cache, 1, 1, False)]
    assert response["data"].name == "reloaded"
    assert response["data"].children[0].id == 10


def _get_get_endpoint(api: BaseAPI[Any, Any, Any]):
    for route in api.router.routes:
        if "GET" in route.methods and route.path == "/dummy/{id}":
            return route.endpoint
    raise AssertionError("get endpoint not found")


def _get_route_operation_id(api: BaseAPI[Any, Any, Any], path: str, method: str) -> str:
    for route in api.router.routes:
        if method in route.methods and route.path == path:
            return route.operation_id
    raise AssertionError(f"{method} {path} route not found")


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


def test_base_api_assigns_stable_operation_ids() -> None:
    api = BaseAPI(
        module_name="test",
        model=DummySoftDeleteModel,
        service=FakeService(),
        response_schema=DummyResponse,
        prefix="/dummy-items",
        gen_create=True,
        gen_update=True,
        gen_delete=True,
        gen_bulk_delete=True,
        enable_permission=False,
    )

    assert _get_route_operation_id(api, "/dummy-items", "POST") == "dummy_items_create"
    assert _get_route_operation_id(api, "/dummy-items/{id}", "PUT") == "dummy_items_update"
    assert _get_route_operation_id(api, "/dummy-items/{id}", "DELETE") == "dummy_items_delete"
    assert _get_route_operation_id(api, "/dummy-items/{id}", "GET") == "dummy_items_get"
    assert _get_route_operation_id(api, "/dummy-items/query", "POST") == "dummy_items_query"
    assert _get_route_operation_id(api, "/dummy-items/bulk", "DELETE") == "dummy_items_bulk_delete"
    assert _get_route_operation_id(api, "/dummy-items/trash", "GET") == "dummy_items_trash"
    assert _get_route_operation_id(api, "/dummy-items/trash/restore", "POST") == "dummy_items_batch_restore"
    assert (
        _get_route_operation_id(api, "/dummy-items/trash/permanent", "DELETE") == "dummy_items_batch_permanent_delete"
    )
    assert _get_route_operation_id(api, "/dummy-items/{id}/restore", "POST") == "dummy_items_restore"


def test_generate_route_operation_id_produces_compact_path_based_ids() -> None:
    app = FastAPI(generate_unique_id_function=generate_route_operation_id)

    @app.post("/api/v1/auth/login")
    async def login() -> dict[str, bool]:
        return {"ok": True}

    @app.put("/api/v1/users/{id}/assign-roles")
    async def assign_roles(id: int) -> dict[str, int]:
        return {"id": id}

    schema = app.openapi()

    assert schema["paths"]["/api/v1/auth/login"]["post"]["operationId"] == "auth_login_post"
    assert schema["paths"]["/api/v1/users/{id}/assign-roles"]["put"]["operationId"] == "users_by_id_assign_roles_put"


def _get_endpoint(api: BaseAPI[Any, Any, Any], path: str, method: str):
    for route in api.router.routes:
        if method in route.methods and route.path == path:
            return route.endpoint
    raise AssertionError(f"{method} {path} endpoint not found")


def _get_route_dependency_permissions(api: BaseAPI[Any, Any, Any], path: str, method: str) -> list[str]:
    for route in api.router.routes:
        if method in route.methods and route.path == path:
            return [getattr(dep.dependency, "permission_required", "") for dep in route.dependencies]
    raise AssertionError(f"{method} {path} route not found")


@pytest.mark.asyncio
async def test_create_endpoint_maps_value_error_to_business_fail_response() -> None:
    service = FakeBatchService()
    service.create_error = ValueError("资源已存在: dummy")
    api = BaseAPI(
        module_name="test",
        model=DummyModel,
        service=service,
        create_schema=DummyCreate,
        response_schema=DummyResponse,
        prefix="/dummy",
        gen_update=False,
        gen_delete=False,
        enable_permission=False,
    )

    endpoint = _get_endpoint(api, "/dummy", "POST")
    response = await endpoint(obj_in=DummyCreate(name="duplicated"), db=object(), cache=object())

    assert response["code"] == "3010"
    assert response["message"] == "资源已存在: dummy"


@pytest.mark.asyncio
async def test_delete_endpoint_maps_value_error_to_standard_fail_response() -> None:
    service = FakeBatchService()
    service.delete_errors = {7: ValueError("当前状态不允许删除")}
    api = BaseAPI(
        module_name="test",
        model=DummySoftDeleteModel,
        service=service,
        response_schema=DummyResponse,
        prefix="/dummy",
        gen_create=False,
        gen_update=False,
        gen_bulk_delete=False,
        enable_permission=False,
    )

    endpoint = _get_endpoint(api, "/dummy/{id}", "DELETE")
    response = await endpoint(id=7, db=object(), cache=object(), permanent=False)

    assert response["code"] == "4001"
    assert response["message"] == "当前状态不允许删除"


@pytest.mark.asyncio
async def test_bulk_delete_counts_false_results_as_failures() -> None:
    service = FakeBatchService()
    service.delete_results = {1: True, 2: False}
    api = BaseAPI(
        module_name="test",
        model=DummySoftDeleteModel,
        service=service,
        response_schema=DummyResponse,
        prefix="/dummy",
        gen_create=False,
        gen_update=False,
        gen_delete=False,
        gen_bulk_delete=True,
        enable_permission=False,
    )

    endpoint = _get_endpoint(api, "/dummy/bulk", "DELETE")
    response = await endpoint(ids=[1, 2], db=object(), cache=object())

    assert response["data"]["success"] == 1
    assert response["data"]["failed"] == 1
    assert response["data"]["errors"] == [{"id": 2, "message": "DummySoftDeleteModel (ID: 2) 不存在或已被删除"}]


@pytest.mark.asyncio
async def test_restore_endpoint_maps_not_found_to_standard_fail_response() -> None:
    service = FakeBatchService()
    service.restore_errors = {7: ValueError("DummySoftDeleteModel 不存在")}
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

    endpoint = _get_endpoint(api, "/dummy/{id}/restore", "POST")
    response = await endpoint(id=7, db=object(), cache=object())

    assert response["code"] == "3000"
    assert response["message"] == "DummySoftDeleteModel (ID: 7) 不存在"


@pytest.mark.asyncio
async def test_tree_move_endpoint_maps_value_error_to_business_fail_response() -> None:
    service = FakeTreeService()
    service.move_error = ValueError("节点 3 不能移动到其后代节点 5 下")
    api = TreeAPI(
        module_name="admin",
        model=DummySoftDeleteModel,
        service=service,
        response_schema=DummyResponse,
        tree_response_schema=DummyResponse,
        prefix="/dummy-tree",
        gen_create=False,
        gen_update=False,
        gen_delete=False,
        enable_permission=False,
    )

    endpoint = _get_endpoint(api, "/dummy-tree/move", "PUT")
    response = await endpoint(db=object(), cache=object(), node_id=3, new_parent_id=5)

    assert response["code"] == "4001"
    assert response["message"] == "节点 3 不能移动到其后代节点 5 下"


@pytest.mark.asyncio
async def test_tree_batch_sort_endpoint_maps_value_error_to_business_fail_response() -> None:
    service = FakeTreeService()
    service.batch_sort_error = ValueError("批量排序形成循环依赖")
    api = TreeAPI(
        module_name="admin",
        model=DummySoftDeleteModel,
        service=service,
        response_schema=DummyResponse,
        tree_response_schema=DummyResponse,
        prefix="/dummy-tree",
        gen_create=False,
        gen_update=False,
        gen_delete=False,
        enable_permission=False,
    )

    endpoint = _get_endpoint(api, "/dummy-tree/batch-sort", "PUT")
    response = await endpoint(
        db=object(),
        cache=object(),
        request=BatchSortRequest(items=[SortItem(id=1, parent_id=2, sort_order=0)]),
    )

    assert response["code"] == "4001"
    assert response["message"] == "批量排序形成循环依赖"


def test_tree_api_uses_tree_permission_for_tree_read_routes() -> None:
    api = TreeAPI(
        module_name="admin",
        model=DummySoftDeleteModel,
        service=FakeTreeService(),
        response_schema=DummyResponse,
        tree_response_schema=DummyResponse,
        prefix="/dummy-tree",
        gen_create=False,
        gen_update=False,
        gen_delete=False,
        enable_permission=True,
    )

    expected_permission = "admin:dummysoftdeletemodel:tree"
    assert _get_route_dependency_permissions(api, "/dummy-tree/tree", "GET") == [expected_permission]
    assert _get_route_dependency_permissions(api, "/dummy-tree/siblings/{node_id}", "GET") == [expected_permission]
    assert _get_route_dependency_permissions(api, "/dummy-tree/ancestors/{node_id}", "GET") == [expected_permission]
    assert _get_route_dependency_permissions(api, "/dummy-tree/children/{node_id}", "GET") == [expected_permission]
