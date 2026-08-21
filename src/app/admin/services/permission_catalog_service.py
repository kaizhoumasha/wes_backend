"""代码权限目录的精确物化服务。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.app.admin.repositories.perm_repository import PermissionRepository, permission_repository
from src.utils.permission_scanner import PermissionCatalogError, build_permission_catalog

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.admin.models import Permission


_SYNC_FIELDS = (
    "description",
    "type",
    "category",
    "resource",
    "action",
    "method",
    "path",
    "parent_id",
    "sort_order",
)


@dataclass(frozen=True, slots=True)
class PermissionCatalogSyncResult:
    created: int
    updated: int
    deleted: int
    unchanged: int
    total: int
    affected_user_ids: frozenset[int] = frozenset()
    affected_app_ids: frozenset[int] = frozenset()


def _parent_name(payload: dict[str, Any]) -> str | None:
    category = payload.get("category")
    resource = payload.get("resource")
    if not category or not resource:
        return None
    if payload.get("action") == "group":
        return None if resource == "system" else f"{category}:system:group"
    if resource == "system":
        return f"{category}:system:group"
    return f"{category}:{resource}:group"


def _update_data(permission: Permission, payload: dict[str, Any]) -> dict[str, Any]:
    return {field: payload.get(field) for field in _SYNC_FIELDS if getattr(permission, field) != payload.get(field)}


def _deletion_layers(nodes: list[Permission]) -> list[list[Permission]]:
    remaining: dict[int, Permission] = {}
    for node in nodes:
        if node.id is None or node.id in remaining:
            raise PermissionCatalogError("权限删除图包含无效或重复节点 ID")
        remaining[node.id] = node

    layers: list[list[Permission]] = []
    while remaining:
        parent_ids = {
            node.parent_id for node in remaining.values() if node.parent_id is not None and node.parent_id in remaining
        }
        leaves = [node for node_id, node in remaining.items() if node_id not in parent_ids]
        if not leaves:
            raise PermissionCatalogError("权限删除图存在环，无法确定叶子节点")
        leaves.sort(key=lambda node: node.id or 0)
        layers.append(leaves)
        for leaf in leaves:
            if leaf.id is not None:
                del remaining[leaf.id]
    return layers


class PermissionCatalogService:
    """把经过验证的代码权限目录精确物化到数据库。"""

    def __init__(self, repository: PermissionRepository = permission_repository):
        self.repository = repository

    async def sync(
        self,
        app: FastAPI,
        db: AsyncSession,
        *,
        dry_run: bool,
    ) -> PermissionCatalogSyncResult:
        catalog = build_permission_catalog(app)

        try:
            existing_nodes = await self.repository.list_catalog_nodes(db)
            desired_names = {payload["name"] for payload in catalog}
            active_by_name: dict[str, Permission] = {}
            for node in existing_nodes:
                if node.is_deleted:
                    continue
                if node.name in active_by_name:
                    raise PermissionCatalogError(f"数据库存在重复活动权限码: `{node.name}`")
                active_by_name[node.name] = node

            deletion_nodes = [node for node in existing_nodes if node.is_deleted or node.name not in desired_names]
            deletion_layers = _deletion_layers(deletion_nodes)

            planned_parent_ids: dict[str, int] = {
                name: node.id for name, node in active_by_name.items() if node.id is not None
            }
            next_temp_id = -1
            planned_updates: list[Permission] = []
            created = 0
            updated = 0
            unchanged = 0
            for desired in catalog:
                payload = dict(desired)
                parent_name = _parent_name(payload)
                payload["parent_id"] = planned_parent_ids.get(parent_name) if parent_name else None
                existing = active_by_name.get(payload["name"])
                if existing is None:
                    created += 1
                    planned_parent_ids[payload["name"]] = next_temp_id
                    next_temp_id -= 1
                elif _update_data(existing, payload):
                    updated += 1
                    planned_updates.append(existing)
                else:
                    unchanged += 1

            if dry_run:
                return PermissionCatalogSyncResult(
                    created=created,
                    updated=updated,
                    deleted=len(deletion_nodes),
                    unchanged=unchanged,
                    total=len(catalog),
                )

            affected_permission_ids = {node.id for node in [*planned_updates, *deletion_nodes] if node.id is not None}
            affected_user_ids, affected_app_ids = await self.repository.collect_catalog_affected_ids(
                db,
                affected_permission_ids,
            )

            resolved_ids: dict[str, int] = {
                name: node.id for name, node in active_by_name.items() if node.id is not None
            }
            for desired in catalog:
                payload = dict(desired)
                parent_name = _parent_name(payload)
                payload["parent_id"] = resolved_ids.get(parent_name) if parent_name else None
                existing = active_by_name.get(payload["name"])

                if existing is None:
                    created_node = await self.repository.create_catalog_node(db, payload)
                    await db.flush()
                    if created_node is None or created_node.id is None:
                        raise PermissionCatalogError(f"创建权限目录节点失败: `{payload['name']}`")
                    active_by_name[created_node.name] = created_node
                    resolved_ids[created_node.name] = created_node.id
                    continue

                update_data = _update_data(existing, payload)
                if update_data:
                    updated_node = await self.repository.update_catalog_node(db, existing, update_data)
                    if updated_node is None:
                        raise PermissionCatalogError(f"更新权限目录节点失败: `{payload['name']}`")
                if existing.id is not None:
                    resolved_ids[existing.name] = existing.id

            await db.flush()
            for layer in deletion_layers:
                for node in layer:
                    if not await self.repository.delete_catalog_node(db, node):
                        raise PermissionCatalogError(f"删除权限目录节点失败: `{node.name}`")
                await db.flush()

            return PermissionCatalogSyncResult(
                created=created,
                updated=updated,
                deleted=len(deletion_nodes),
                unchanged=unchanged,
                total=len(catalog),
                affected_user_ids=affected_user_ids,
                affected_app_ids=affected_app_ids,
            )
        except Exception:
            await db.rollback()
            raise


permission_catalog_service = PermissionCatalogService()

__all__ = [
    "PermissionCatalogService",
    "PermissionCatalogSyncResult",
    "permission_catalog_service",
]
