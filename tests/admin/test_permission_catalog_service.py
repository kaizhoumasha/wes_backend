from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import Depends, FastAPI

from src.app.admin.services.permission_catalog_service import (
    PermissionCatalogService,
    PermissionCatalogSyncResult,
)
from src.utils.permission_scanner import PermissionCatalogError


def _catalog_app() -> FastAPI:
    app = FastAPI()

    async def require_permission() -> None:
        return None

    require_permission.permission_required = "ops:item:list"  # type: ignore[attr-defined]
    require_permission.is_rbac = True  # type: ignore[attr-defined]

    async def list_items() -> None:
        return None

    app.add_api_route(
        "/items",
        list_items,
        methods=["GET"],
        dependencies=[Depends(require_permission)],
        summary="List items",
    )
    return app


def _node(
    *,
    node_id: int,
    name: str,
    description: str,
    resource: str,
    action: str,
    path: str,
    parent_id: int | None,
    sort_order: int,
    is_deleted: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=node_id,
        name=name,
        description=description,
        type="user_api",
        category="ops",
        resource=resource,
        action=action,
        method="GET",
        path=path,
        parent_id=parent_id,
        sort_order=sort_order,
        is_deleted=is_deleted,
        version=1,
    )


def _repository(*, nodes: list[SimpleNamespace]) -> SimpleNamespace:
    async def create_catalog_node(_db: object, payload: dict[str, object]) -> SimpleNamespace:
        return SimpleNamespace(id=4, is_deleted=False, version=1, **payload)

    return SimpleNamespace(
        list_catalog_nodes=AsyncMock(return_value=nodes),
        collect_catalog_affected_ids=AsyncMock(return_value=(frozenset({7}), frozenset({9}))),
        create_catalog_node=AsyncMock(side_effect=create_catalog_node),
        update_catalog_node=AsyncMock(),
        delete_catalog_node=AsyncMock(),
    )


async def test_sync_materializes_exact_catalog_without_committing() -> None:
    category = _node(
        node_id=1,
        name="ops:system:group",
        description="ops 模块权限分组",
        resource="system",
        action="group",
        path="/ops",
        parent_id=None,
        sort_order=1,
    )
    resource = _node(
        node_id=2,
        name="ops:item:group",
        description="旧说明",
        resource="item",
        action="group",
        path="/ops/item",
        parent_id=1,
        sort_order=1,
    )
    stale = _node(
        node_id=3,
        name="ops:stale:list",
        description="stale",
        resource="stale",
        action="list",
        path="/stale",
        parent_id=None,
        sort_order=1,
    )
    repository = _repository(nodes=[category, resource, stale])
    db = AsyncMock()

    result = await PermissionCatalogService(repository).sync(_catalog_app(), db, dry_run=False)

    assert result == PermissionCatalogSyncResult(
        created=1,
        updated=1,
        deleted=1,
        unchanged=1,
        total=3,
        affected_user_ids=frozenset({7}),
        affected_app_ids=frozenset({9}),
    )
    repository.create_catalog_node.assert_awaited_once()
    assert repository.create_catalog_node.await_args.args[1]["name"] == "ops:item:list"
    repository.update_catalog_node.assert_awaited_once()
    assert repository.update_catalog_node.await_args.args[1] is resource
    assert repository.update_catalog_node.await_args.args[2] == {"description": "item 权限分组"}
    repository.delete_catalog_node.assert_awaited_once_with(db, stale)
    assert db.flush.await_count >= 1
    db.commit.assert_not_awaited()


async def test_sync_dry_run_reports_plan_without_mutations() -> None:
    repository = _repository(nodes=[])
    db = AsyncMock()

    result = await PermissionCatalogService(repository).sync(_catalog_app(), db, dry_run=True)

    assert result == PermissionCatalogSyncResult(created=3, updated=0, deleted=0, unchanged=0, total=3)
    repository.create_catalog_node.assert_not_awaited()
    repository.update_catalog_node.assert_not_awaited()
    repository.delete_catalog_node.assert_not_awaited()
    db.flush.assert_not_awaited()
    db.commit.assert_not_awaited()


async def test_sync_scanner_failure_performs_no_repository_mutation() -> None:
    repository = _repository(nodes=[])
    db = AsyncMock()

    with pytest.raises(PermissionCatalogError, match="未扫描到权限"):
        await PermissionCatalogService(repository).sync(FastAPI(), db, dry_run=False)

    repository.list_catalog_nodes.assert_not_awaited()
    repository.create_catalog_node.assert_not_awaited()
    repository.update_catalog_node.assert_not_awaited()
    repository.delete_catalog_node.assert_not_awaited()
    db.flush.assert_not_awaited()
    db.commit.assert_not_awaited()


async def test_sync_rejects_cycle_in_actual_parent_graph_and_rolls_back() -> None:
    first = _node(
        node_id=10,
        name="stale:first:list",
        description="first",
        resource="first",
        action="list",
        path="/first",
        parent_id=11,
        sort_order=1,
    )
    second = _node(
        node_id=11,
        name="stale:second:list",
        description="second",
        resource="second",
        action="list",
        path="/second",
        parent_id=10,
        sort_order=1,
    )
    repository = _repository(nodes=[first, second])
    db = AsyncMock()

    with pytest.raises(PermissionCatalogError, match="删除图存在环"):
        await PermissionCatalogService(repository).sync(_catalog_app(), db, dry_run=False)

    repository.create_catalog_node.assert_not_awaited()
    repository.update_catalog_node.assert_not_awaited()
    repository.delete_catalog_node.assert_not_awaited()
    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()
