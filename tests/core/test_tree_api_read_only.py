from typing import Any

from pydantic import BaseModel

from src.core.tree_api import TreeAPI


class DummyTreeModel:
    pass


class DummyTreeResponse(BaseModel):
    id: int


def test_tree_update_routes_are_absent_when_update_generation_is_disabled() -> None:
    api = TreeAPI(
        module_name="test",
        model=DummyTreeModel,
        service=object(),
        response_schema=DummyTreeResponse,
        tree_response_schema=DummyTreeResponse,
        prefix="/readonly-tree",
        gen_create=False,
        gen_update=False,
        gen_delete=False,
        enable_permission=False,
    )

    routes = {(route.path, method) for route in api.router.routes for method in route.methods}

    assert ("/readonly-tree/tree", "GET") in routes
    assert ("/readonly-tree/siblings/{node_id}", "GET") in routes
    assert ("/readonly-tree/ancestors/{node_id}", "GET") in routes
    assert ("/readonly-tree/children/{node_id}", "GET") in routes
    assert ("/readonly-tree/move", "PUT") not in routes
    assert ("/readonly-tree/batch-sort", "PUT") not in routes
