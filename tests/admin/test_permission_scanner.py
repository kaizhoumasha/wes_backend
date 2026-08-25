from __future__ import annotations

from collections import defaultdict

import pytest
from fastapi import Depends, FastAPI

from src.core.base_api import BaseAPI
from src.register import create_app
from src.utils import permission_scanner as permission_scanner_module
from src.utils.permission_scanner import scan_routes_for_permissions


class DummySoftDeleteModel:
    is_deleted = True

    def soft_delete(self, deleted_by: int | None = None) -> None:
        return None

    def restore(self) -> None:
        return None


class _FakeService:
    pass


def _permission_dependency(permission_name: str, *, permission_type: str = "user_api") -> object:
    async def dependency() -> None:
        return None

    dependency.permission_required = permission_name  # type: ignore[attr-defined]
    if permission_type == "app_api":
        dependency.is_api_auth = True  # type: ignore[attr-defined]
    else:
        dependency.is_rbac = True  # type: ignore[attr-defined]
    return dependency


def _add_permission_route(
    app: FastAPI,
    *,
    path: str,
    permission_name: str,
    methods: list[str] | None = None,
    permission_type: str = "user_api",
    summary: str | None = None,
) -> None:
    async def endpoint() -> None:
        return None

    app.add_api_route(
        path,
        endpoint,
        methods=methods or ["GET"],
        dependencies=[Depends(_permission_dependency(permission_name, permission_type=permission_type))],
        summary=summary,
    )


def test_build_permission_catalog_rejects_empty_scan() -> None:
    with pytest.raises(permission_scanner_module.PermissionCatalogError, match="未扫描到权限"):
        permission_scanner_module.build_permission_catalog(FastAPI())


def test_build_validated_permission_leaves_rejects_empty_scan() -> None:
    with pytest.raises(permission_scanner_module.PermissionCatalogError, match="未扫描到权限"):
        permission_scanner_module.build_validated_permission_leaves(FastAPI())


def test_build_permission_catalog_rejects_conflicting_duplicate_permission_names() -> None:
    app = FastAPI()
    _add_permission_route(app, path="/first", permission_name="admin:user:list")
    _add_permission_route(app, path="/second", permission_name="admin:user:list", methods=["POST"])

    with pytest.raises(permission_scanner_module.PermissionCatalogError, match="重复权限码"):
        permission_scanner_module.build_permission_catalog(app)


def test_build_validated_permission_leaves_rejects_conflicting_duplicate_permission_names() -> None:
    app = FastAPI()
    _add_permission_route(app, path="/first", permission_name="admin:user:list")
    _add_permission_route(app, path="/second", permission_name="admin:user:list", methods=["POST"])

    with pytest.raises(permission_scanner_module.PermissionCatalogError, match="重复权限码"):
        permission_scanner_module.build_validated_permission_leaves(app)


def test_build_validated_permission_leaves_collapses_identical_duplicates_deterministically() -> None:
    permission = {
        "name": "admin:user:list",
        "type": "user_api",
        "category": "admin",
        "description": "用户列表",
        "resource": "user",
        "action": "list",
        "method": "GET",
        "path": "/users",
    }
    app = FastAPI()

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        permission_scanner_module,
        "scan_routes_for_permissions",
        lambda _app: [dict(permission), dict(permission)],
    )
    try:
        first = permission_scanner_module.build_validated_permission_leaves(app)
        second = permission_scanner_module.build_validated_permission_leaves(app)
    finally:
        monkeypatch.undo()

    assert first == second == [permission]


@pytest.mark.parametrize(
    "permission_name",
    [
        "admin:user",
        "admin:user:list:extra",
        ":user:list",
        "admin::list",
        "admin:user:",
    ],
    ids=["too-few", "too-many", "empty-module", "empty-resource", "empty-action"],
)
def test_build_permission_catalog_rejects_malformed_permission_names(permission_name: str) -> None:
    app = FastAPI()
    _add_permission_route(app, path="/users", permission_name=permission_name)

    with pytest.raises(permission_scanner_module.PermissionCatalogError, match="权限码格式无效"):
        permission_scanner_module.build_permission_catalog(app)


@pytest.mark.parametrize(
    "permission_name",
    ["admin:user", "admin:user:list:extra", ":user:list", "admin::list", "admin:user:"],
)
def test_build_validated_permission_leaves_rejects_malformed_permission_names(permission_name: str) -> None:
    app = FastAPI()
    _add_permission_route(app, path="/users", permission_name=permission_name)

    with pytest.raises(permission_scanner_module.PermissionCatalogError, match="权限码格式无效"):
        permission_scanner_module.build_validated_permission_leaves(app)


@pytest.mark.parametrize("field", ["type", "method", "path"])
def test_build_permission_catalog_rejects_missing_required_leaf_fields(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    permission = {
        "name": "admin:user:list",
        "type": "user_api",
        "category": "admin",
        "description": "List users",
        "resource": "user",
        "action": "list",
        "method": "GET",
        "path": "/users",
    }
    permission[field] = None
    monkeypatch.setattr(
        permission_scanner_module,
        "scan_routes_for_permissions",
        lambda _app: [permission],
    )

    with pytest.raises(permission_scanner_module.PermissionCatalogError, match=f"权限叶子字段无效.*{field}"):
        permission_scanner_module.build_permission_catalog(FastAPI())


@pytest.mark.parametrize("field", ["type", "category", "description", "resource", "action", "method", "path"])
def test_build_validated_permission_leaves_rejects_missing_required_leaf_fields(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    permission = {
        "name": "admin:user:list",
        "type": "user_api",
        "category": "admin",
        "description": "List users",
        "resource": "user",
        "action": "list",
        "method": "GET",
        "path": "/users",
    }
    permission[field] = None
    monkeypatch.setattr(permission_scanner_module, "scan_routes_for_permissions", lambda _app: [permission])

    with pytest.raises(permission_scanner_module.PermissionCatalogError, match=f"权限叶子字段无效.*{field}"):
        permission_scanner_module.build_validated_permission_leaves(FastAPI())


def test_build_validated_permission_leaves_rejects_extra_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    permission = {
        "name": "admin:user:list",
        "type": "user_api",
        "category": "admin",
        "description": "List users",
        "resource": "user",
        "action": "list",
        "method": "GET",
        "path": "/users",
        "unexpected": "must fail closed",
    }
    monkeypatch.setattr(permission_scanner_module, "scan_routes_for_permissions", lambda _app: [permission])

    with pytest.raises(permission_scanner_module.PermissionCatalogError, match="字段集合无效"):
        permission_scanner_module.build_validated_permission_leaves(FastAPI())


def test_build_validated_permission_leaves_uses_provider_contract_order(monkeypatch: pytest.MonkeyPatch) -> None:
    first_by_name = {
        "name": "alpha:item:list",
        "type": "user_api",
        "category": "alpha",
        "description": "Alpha items",
        "resource": "item",
        "action": "list",
        "method": "GET",
        "path": "/alpha/items",
    }
    first_by_catalog_presentation = {
        "name": "zulu:item:list",
        "type": "app_api",
        "category": "zulu",
        "description": "Zulu items",
        "resource": "item",
        "action": "list",
        "method": "GET",
        "path": "/zulu/items",
    }
    monkeypatch.setattr(
        permission_scanner_module,
        "scan_routes_for_permissions",
        lambda _app: [first_by_catalog_presentation, first_by_name],
    )

    leaves = permission_scanner_module.build_validated_permission_leaves(FastAPI())

    assert [leaf["name"] for leaf in leaves] == ["alpha:item:list", "zulu:item:list"]


def test_build_permission_catalog_is_ordered_and_selects_http_method_deterministically() -> None:
    app = FastAPI()
    _add_permission_route(
        app,
        path="/widgets/{widget_id}",
        permission_name="ops:widget:update",
        methods=["POST", "GET"],
    )
    _add_permission_route(app, path="/aisles", permission_name="ops:aisle:list")

    first = permission_scanner_module.build_permission_catalog(app)
    second = permission_scanner_module.build_permission_catalog(app)

    assert first == second
    assert [payload["name"] for payload in first] == [
        "ops:system:group",
        "ops:aisle:group",
        "ops:widget:group",
        "ops:aisle:list",
        "ops:widget:update",
    ]
    assert next(payload for payload in first if payload["name"] == "ops:widget:update")["method"] == "GET"


def test_build_permission_catalog_reuses_validated_leaves_and_only_catalog_adds_groups() -> None:
    app = FastAPI()
    _add_permission_route(app, path="/users", permission_name="admin:user:list", summary="用户列表")

    leaves = permission_scanner_module.build_validated_permission_leaves(app)
    catalog = permission_scanner_module.build_permission_catalog(app)
    catalog_leaves = [payload for payload in catalog if payload["action"] != "group"]

    assert all(set(payload) == set(leaves[0]) for payload in leaves)
    assert [
        {key: value for key, value in payload.items() if key not in {"parent_id", "sort_order"}}
        for payload in catalog_leaves
    ] == leaves
    assert [payload["name"] for payload in catalog if payload["action"] == "group"] == [
        "admin:system:group",
        "admin:user:group",
    ]


def test_build_permission_catalog_rejects_leaf_and_generated_group_name_collision() -> None:
    app = FastAPI()
    _add_permission_route(
        app,
        path="/ops/widget",
        permission_name="ops:widget:group",
        summary="widget 权限分组",
    )

    with pytest.raises(permission_scanner_module.PermissionCatalogError, match="权限目录名称冲突"):
        permission_scanner_module.build_permission_catalog(app)


def test_build_permission_catalog_rejects_generated_group_payload_collision() -> None:
    app = FastAPI()
    _add_permission_route(app, path="/internal/items", permission_name="shared:item:list")
    _add_permission_route(
        app,
        path="/external/items",
        permission_name="shared:item:create",
        methods=["POST"],
        permission_type="app_api",
    )

    with pytest.raises(permission_scanner_module.PermissionCatalogError, match="权限目录名称冲突"):
        permission_scanner_module.build_permission_catalog(app)


def test_full_application_permission_routes_are_unique_and_catalog_builds() -> None:
    app = create_app()
    definitions_by_name: dict[str, set[tuple[str, str | None, str | None]]] = defaultdict(set)
    for permission in permission_scanner_module.scan_routes_for_permissions(app):
        definitions_by_name[permission["name"]].add(
            (permission["type"], permission.get("method"), permission.get("path"))
        )

    duplicate_definitions = {
        name: definitions for name, definitions in definitions_by_name.items() if len(definitions) > 1
    }

    assert duplicate_definitions == {}
    catalog = permission_scanner_module.build_permission_catalog(app)
    assert len({payload["name"] for payload in catalog}) == len(catalog)


def test_full_application_permission_catalog_excludes_retired_external_callback_permission() -> None:
    permission_names = {permission["name"] for permission in scan_routes_for_permissions(create_app())}

    assert "api:callback:event" not in permission_names


def test_scan_routes_for_permissions_excludes_permanent_delete_when_delete_generation_is_disabled() -> None:
    app = FastAPI()
    api = BaseAPI(
        module_name="test",
        model=DummySoftDeleteModel,
        service=_FakeService(),
        response_schema=dict,
        prefix="/dummy-items",
        gen_create=False,
        gen_update=False,
        gen_delete=False,
        enable_permission=True,
    )
    app.include_router(api.router)

    scanned = scan_routes_for_permissions(app)
    by_name = {item["name"]: item for item in scanned}

    assert "test:dummysoftdeletemodel:permanent_delete" not in by_name


def test_scan_routes_for_permissions_uses_api_application_permission_resource_override() -> None:
    app = FastAPI()
    api = BaseAPI(
        module_name="api-auth",
        model=DummySoftDeleteModel,
        service=_FakeService(),
        response_schema=dict,
        prefix="/api-applications",
        permission_resource="api_application",
        enable_permission=True,
    )
    app.include_router(api.router, prefix="/api")

    scanned_names = {item["name"] for item in scan_routes_for_permissions(app)}

    assert "api-auth:api_application:list" in scanned_names
    assert "api-auth:api_application:detail" in scanned_names
    assert "api-auth:api_application:permanent_delete" in scanned_names
    assert not any(":apiapplication:" in name for name in scanned_names)
