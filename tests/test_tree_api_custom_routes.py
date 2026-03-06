from types import SimpleNamespace

from fastapi import APIRouter

from src.core.tree_api import TreeAPI


class DummyModel:
    pass


def register_my_route(router: APIRouter, _api: TreeAPI) -> None:
    @router.get("/my")
    async def my_route() -> dict[str, str]:
        return {"status": "ok"}


def test_tree_api_registers_custom_routes() -> None:
    api = TreeAPI(
        module_name="test",
        model=DummyModel,
        service=SimpleNamespace(),
        prefix="/dummy",
        gen_create=False,
        gen_update=False,
        gen_delete=False,
        enable_permission=False,
        custom_routes=[register_my_route],
    )

    paths = {route.path for route in api.router.routes}
    assert "/dummy/my" in paths
