from inspect import signature
from typing import Any

from src.core.base_api import BaseAPI
from src.core.service_protocols import CrudServiceProtocol, TreeServiceProtocol
from src.core.tree_api import TreeAPI


class FakeCrudService:
    response_schema = None

    async def get_by_id(
        self,
        db: object,
        cache: object,
        id: int,
        max_depth: int = 2,
        include_deleted: bool = False,
    ) -> dict[str, Any] | None:
        return None

    async def get_list(
        self,
        db: object,
        cache: object,
        limit: int = 10,
        offset: int = 0,
        filters: object | None = None,
        sort: list[object] | None = None,
        max_depth: int = 1,
        include_deleted: bool = False,
    ) -> tuple[int, list[dict[str, Any]]]:
        return 0, []

    async def create(self, db: object, data: dict[str, Any], cache: object | None = None) -> dict[str, Any] | None:
        return data

    async def update(
        self,
        db: object,
        id: int,
        data: dict[str, Any],
        cache: object | None = None,
    ) -> dict[str, Any] | None:
        return data

    async def delete(self, db: object, id: int, cache: object | None = None) -> bool | None:
        return True

    async def restore(self, db: object, id: int, cache: object | None = None) -> dict[str, Any] | None:
        return {"id": id}

    async def get_deleted(self, db: object, limit: int = 10, offset: int = 0) -> tuple[int, list[dict[str, Any]]]:
        return 0, []

    async def permanent_delete(self, db: object, id: int, cache: object | None = None) -> bool:
        return True

    def to_response(self, model: object, response_schema: type) -> object:
        return model

    def to_list_response(self, models: list[object], response_schema: type) -> list[object]:
        return models


class FakeTreeService(FakeCrudService):
    async def get_tree(self, db: object, root_id: int | None = None, max_depth: int = -1) -> list[dict[str, Any]]:
        return []

    async def get_siblings(
        self,
        db: object,
        node_id: int,
        include_self: bool = False,
    ) -> list[dict[str, Any]]:
        return []

    async def get_ancestors(
        self,
        db: object,
        node_id: int,
        include_self: bool = False,
    ) -> list[dict[str, Any]]:
        return []

    async def move_node(self, db: object, node_id: int, new_parent_id: int | None) -> dict[str, Any]:
        return {"id": node_id, "parent_id": new_parent_id}


def test_crud_service_protocol_is_runtime_checkable() -> None:
    assert isinstance(FakeCrudService(), CrudServiceProtocol)


def test_tree_service_protocol_extends_crud_contract() -> None:
    assert isinstance(FakeTreeService(), TreeServiceProtocol)


def test_base_api_constructor_depends_on_crud_protocol() -> None:
    service_annotation = signature(BaseAPI.__init__).parameters["service"].annotation
    assert service_annotation is CrudServiceProtocol


def test_tree_api_constructor_depends_on_tree_protocol() -> None:
    service_annotation = signature(TreeAPI.__init__).parameters["service"].annotation
    assert service_annotation is TreeServiceProtocol
