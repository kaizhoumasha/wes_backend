"""权限目录在隔离 PostgreSQL 上的精确物化验收。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from fastapi import Depends, FastAPI
from sqlalchemy import delete, insert, select

from src.app.admin.models import Permission, Role, role_permission
from src.app.admin.repositories.perm_repository import PermissionRepository
from src.app.admin.services import PermissionCatalogService
from src.app.api_auth.models import APIApplication, api_app_permissions

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.integration


def _catalog_app(category: str) -> FastAPI:
    app = FastAPI()

    async def require_permission() -> None:
        return None

    require_permission.permission_required = f"{category}:item:list"  # type: ignore[attr-defined]
    require_permission.is_rbac = True  # type: ignore[attr-defined]

    async def list_items() -> None:
        return None

    app.add_api_route(
        f"/{category}/items",
        list_items,
        dependencies=[Depends(require_permission)],
        methods=["GET"],
        summary="List items",
    )
    return app


def _permission(
    *,
    name: str,
    description: str,
    category: str,
    resource: str,
    action: str,
    path: str,
    parent_id: int | None = None,
    is_deleted: bool = False,
) -> Permission:
    return Permission(
        name=name,
        description=description,
        type="user_api",
        category=category,
        resource=resource,
        action=action,
        method="GET",
        path=path,
        parent_id=parent_id,
        sort_order=1,
        is_deleted=is_deleted,
    )


def _role_and_app(prefix: str) -> tuple[Role, APIApplication]:
    return (
        Role(name=f"{prefix}-role"),
        APIApplication(
            app_name=f"{prefix}-app",
            app_id=f"{prefix}-app-id",
            app_secret_encrypted="encrypted-secret",
        ),
    )


async def _link_permission(db: AsyncSession, permission: Permission, role: Role, app: APIApplication) -> None:
    assert permission.id is not None
    assert role.id is not None
    assert app.id is not None
    await db.execute(insert(role_permission).values(role_id=role.id, permission_id=permission.id))
    await db.execute(insert(api_app_permissions).values(app_id=app.id, permission_id=permission.id))
    await db.flush()


async def _assert_links_absent(db: AsyncSession, permission_id: int) -> None:
    assert (
        await db.execute(select(role_permission).where(role_permission.c.permission_id == permission_id))
    ).first() is None
    assert (
        await db.execute(select(api_app_permissions).where(api_app_permissions.c.permission_id == permission_id))
    ).first() is None


class _RecordingPermissionRepository(PermissionRepository):
    def __init__(self) -> None:
        super().__init__()
        self.deleted_names: list[str] = []

    async def delete_catalog_node(self, db: AsyncSession, permission: Permission) -> bool:
        self.deleted_names.append(permission.name)
        return await super().delete_catalog_node(db, permission)


class _FailAfterUpdatePermissionRepository(PermissionRepository):
    async def update_catalog_node(
        self,
        db: AsyncSession,
        permission: Permission,
        update_data: dict[str, Any],
    ) -> Permission | None:
        _ = await super().update_catalog_node(db, permission, update_data)
        raise RuntimeError("injected catalog repository failure")


async def test_exact_sync_deletes_stale_permission_and_fk_relations(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with integration_session_factory() as db:
        stale = _permission(
            name="retired:item:list",
            description="retired",
            category="retired",
            resource="item",
            action="list",
            path="/retired/items",
        )
        role, api_app = _role_and_app("catalog-stale")
        db.add_all([stale, role, api_app])
        await db.flush()
        assert stale.id is not None
        stale_id = stale.id
        await _link_permission(db, stale, role, api_app)

        result = await PermissionCatalogService().sync(_catalog_app("catalog-stale"), db, dry_run=False)

        assert result.deleted == 1
        assert await db.get(Permission, stale_id) is None
        await _assert_links_absent(db, stale_id)
        await db.rollback()


async def test_exact_sync_replaces_desired_tombstone_without_inheriting_authorization_links(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with integration_session_factory() as db:
        tombstone = _permission(
            name="catalog-replace:item:list",
            description="List items",
            category="catalog-replace",
            resource="item",
            action="list",
            path="/catalog-replace/items",
            is_deleted=True,
        )
        role, api_app = _role_and_app("catalog-replace")
        db.add_all([tombstone, role, api_app])
        await db.flush()
        assert tombstone.id is not None
        tombstone_id = tombstone.id
        await _link_permission(db, tombstone, role, api_app)

        await PermissionCatalogService().sync(_catalog_app("catalog-replace"), db, dry_run=False)

        replacement = (
            await db.execute(
                select(Permission).where(
                    Permission.name == "catalog-replace:item:list",
                    Permission.is_deleted.is_(False),
                )
            )
        ).scalar_one()
        assert replacement.id is not None
        assert replacement.id != tombstone_id
        assert await db.get(Permission, tombstone_id) is None
        await _assert_links_absent(db, tombstone_id)
        await _assert_links_absent(db, replacement.id)
        await db.rollback()


async def test_exact_sync_reparents_active_leaf_through_replacement_groups_before_deleting_tombstones(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with integration_session_factory() as db:
        old_category = _permission(
            name="catalog-reparent:system:group",
            description="catalog-reparent 模块权限分组",
            category="catalog-reparent",
            resource="system",
            action="group",
            path="/catalog-reparent",
            is_deleted=True,
        )
        db.add(old_category)
        await db.flush()
        assert old_category.id is not None
        old_category.tree_path = f"/{old_category.id}/"
        old_resource = _permission(
            name="catalog-reparent:item:group",
            description="item 权限分组",
            category="catalog-reparent",
            resource="item",
            action="group",
            path="/catalog-reparent/item",
            parent_id=old_category.id,
            is_deleted=True,
        )
        db.add(old_resource)
        await db.flush()
        assert old_resource.id is not None
        old_resource.tree_path = f"/{old_category.id}/{old_resource.id}/"
        old_resource.level = 2
        active_leaf = _permission(
            name="catalog-reparent:item:list",
            description="List items",
            category="catalog-reparent",
            resource="item",
            action="list",
            path="/catalog-reparent/items",
            parent_id=old_resource.id,
        )
        db.add(active_leaf)
        await db.flush()
        assert active_leaf.id is not None
        active_leaf.tree_path = f"/{old_category.id}/{old_resource.id}/{active_leaf.id}/"
        active_leaf.level = 3
        await db.flush()
        old_category_id = old_category.id
        old_resource_id = old_resource.id
        leaf_id = active_leaf.id
        assert leaf_id is not None

        repository = _RecordingPermissionRepository()
        await PermissionCatalogService(repository).sync(_catalog_app("catalog-reparent"), db, dry_run=False)

        new_category = (
            await db.execute(
                select(Permission).where(
                    Permission.name == "catalog-reparent:system:group",
                    Permission.is_deleted.is_(False),
                )
            )
        ).scalar_one()
        new_resource = (
            await db.execute(
                select(Permission).where(
                    Permission.name == "catalog-reparent:item:group",
                    Permission.is_deleted.is_(False),
                )
            )
        ).scalar_one()
        reparented_leaf = await db.get(Permission, leaf_id)
        assert new_category.id not in {None, old_category_id}
        assert new_resource.id not in {None, old_resource_id}
        assert new_resource.parent_id == new_category.id
        assert reparented_leaf is not None
        assert reparented_leaf.parent_id == new_resource.id
        assert await db.get(Permission, old_resource_id) is None
        assert await db.get(Permission, old_category_id) is None
        assert repository.deleted_names == [
            "catalog-reparent:item:group",
            "catalog-reparent:system:group",
        ]
        await db.rollback()


async def test_exact_sync_deletes_same_type_child_before_parent_from_actual_parent_graph(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with integration_session_factory() as db:
        stale_parent = _permission(
            name="stale:parent:list",
            description="parent",
            category="stale",
            resource="parent",
            action="list",
            path="/stale/parent",
        )
        db.add(stale_parent)
        await db.flush()
        assert stale_parent.id is not None
        stale_parent.tree_path = f"/{stale_parent.id}/"
        stale_child = _permission(
            name="stale:child:list",
            description="child",
            category="stale",
            resource="child",
            action="list",
            path="/stale/child",
            parent_id=stale_parent.id,
        )
        db.add(stale_child)
        await db.flush()
        assert stale_child.id is not None
        stale_child.tree_path = f"/{stale_parent.id}/{stale_child.id}/"
        stale_child.level = 2
        await db.flush()

        repository = _RecordingPermissionRepository()
        await PermissionCatalogService(repository).sync(_catalog_app("catalog-order"), db, dry_run=False)

        assert repository.deleted_names == ["stale:child:list", "stale:parent:list"]
        assert await db.get(Permission, stale_child.id) is None
        assert await db.get(Permission, stale_parent.id) is None
        await db.rollback()


async def test_repository_failure_after_update_rolls_back_to_pre_sync_state(
    integration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    category_name = "catalog-rollback:system:group"
    async with integration_session_factory() as setup_db:
        category = _permission(
            name=category_name,
            description="pre-sync description",
            category="catalog-rollback",
            resource="system",
            action="group",
            path="/catalog-rollback",
        )
        setup_db.add(category)
        await setup_db.commit()
        category_id = category.id
        assert category_id is not None

    try:
        async with integration_session_factory() as db:
            with pytest.raises(RuntimeError, match="injected catalog repository failure"):
                await PermissionCatalogService(_FailAfterUpdatePermissionRepository()).sync(
                    _catalog_app("catalog-rollback"),
                    db,
                    dry_run=False,
                )

        async with integration_session_factory() as assertion_db:
            persisted = await assertion_db.get(Permission, category_id)
            assert persisted is not None
            assert persisted.description == "pre-sync description"
    finally:
        async with integration_session_factory() as cleanup_db:
            await cleanup_db.execute(delete(Permission).where(Permission.name.like("catalog-rollback:%")))
            await cleanup_db.commit()
