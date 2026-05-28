from collections.abc import Callable
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.admin.models import Permission, Role, role_permission
from src.app.admin.repositories.perm_repository import PermissionRepository
from src.core.logger import logger

_READ_ONLY_SUFFIXES = (":list", ":detail", ":tree")
_USER_READ_ONLY_SUFFIXES = (":list", ":detail")
_SYNC_FIELDS = ("description", "type", "category", "resource", "action", "method", "path", "parent_id", "sort_order")
_ROLE_PERMISSION_RULES: dict[str, Callable[[Permission], bool]] = {
    "系统管理员": lambda _permission: True,
    "管理员": lambda permission: permission.name.startswith("admin:"),
    "运营人员": lambda permission: permission.name.endswith(_READ_ONLY_SUFFIXES),
    "财务人员": lambda permission: permission.name.startswith("admin:audit:"),
    "普通用户": lambda permission: permission.name.endswith(_USER_READ_ONLY_SUFFIXES),
}


def _normalize_path_segment(value: str) -> str:
    return value.replace("_", "-")


def _clean_summary(summary: str | None) -> str | None:
    """去除 summary 中的权限码前缀 [xxx]"""
    if not summary:
        return None
    if summary.startswith("[") and "] " in summary:
        return summary.split("] ", 1)[1]
    return summary


def _build_permission_record(route: APIRoute, permission_name: str, permission_type: str) -> dict[str, Any]:
    parts = permission_name.split(":")
    return {
        "name": permission_name,
        "type": permission_type,
        "category": parts[0] if len(parts) >= 2 else None,
        "description": _clean_summary(route.summary) or route.name,
        "resource": parts[1] if len(parts) >= 2 else "unknown",
        "action": parts[-1] if len(parts) >= 3 else "unknown",
        "method": next(iter(route.methods)) if route.methods else None,
        "path": route.path,
    }


def scan_routes_for_permissions(app: FastAPI) -> list[dict[str, Any]]:
    """扫描 FastAPI 应用中的所有路由，提取权限信息"""
    permissions_found: list[dict[str, Any]] = []
    seen_permissions: set[str] = set()

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue

        for dep in route.dependencies:
            dependency_obj = dep.dependency
            perm_name = getattr(dependency_obj, "permission_required", None)
            if not perm_name or perm_name in seen_permissions:
                continue

            is_api_auth = getattr(dependency_obj, "is_api_auth", False)
            is_rbac = getattr(dependency_obj, "is_rbac", False)
            is_superuser = getattr(dependency_obj, "is_superuser", False)

            if is_api_auth:
                seen_permissions.add(perm_name)
                permissions_found.append(_build_permission_record(route, perm_name, "app_api"))
            elif is_rbac:
                seen_permissions.add(perm_name)
                permissions_found.append(_build_permission_record(route, perm_name, "user_api"))
            elif is_superuser:
                continue

    return permissions_found


def build_permission_preview_rows(permissions: list[dict[str, Any]]) -> list[str]:
    """生成权限预览文本行"""
    rows: list[str] = []
    for permission in permissions:
        method = permission.get("method") or "?"
        path = permission.get("path") or "-"
        description = permission.get("description") or ""
        rows.extend(
            [
                f"🔐 [{permission['type']}] {permission['name']}",
                f"      {method:<6} {path}",
                f"      说明: {description}",
                "",
            ]
        )
    return rows


def _build_category_group_payload(permission_type: str, category: str, sort_order: int) -> dict[str, Any]:
    return {
        "name": f"{category}:system:group",
        "description": f"{category} 模块权限分组",
        "type": permission_type,
        "category": category,
        "resource": "system",
        "action": "group",
        "method": "GET",
        "path": f"/{_normalize_path_segment(category)}",
        "parent_id": None,
        "sort_order": sort_order,
    }


def _build_resource_group_payload(
    permission_type: str,
    category: str,
    resource: str,
    parent_id: int | None,
    sort_order: int,
) -> dict[str, Any] | None:
    if resource == "system":
        return None

    return {
        "name": f"{category}:{resource}:group",
        "description": f"{resource} 权限分组",
        "type": permission_type,
        "category": category,
        "resource": resource,
        "action": "group",
        "method": "GET",
        "path": f"/{_normalize_path_segment(category)}/{_normalize_path_segment(resource)}",
        "parent_id": parent_id,
        "sort_order": sort_order,
    }


def _build_group_payloads(scanned_perms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    category_orders: dict[str, int] = {}
    resource_orders: dict[str, int] = {}
    seen_category_groups: set[str] = set()
    seen_resource_groups: set[str] = set()

    for permission in scanned_perms:
        permission_type = permission.get("type")
        category = permission.get("category")
        resource = permission.get("resource")
        if not permission_type or not category or not resource:
            continue

        category_group_name = f"{category}:system:group"
        if category_group_name not in seen_category_groups:
            category_orders[permission_type] = category_orders.get(permission_type, 0) + 1
            payloads.append(
                _build_category_group_payload(permission_type, category, sort_order=category_orders[permission_type])
            )
            seen_category_groups.add(category_group_name)

        resource_group_name = f"{category}:{resource}:group"
        if resource == "system" or resource_group_name in seen_resource_groups:
            continue

        resource_order_key = f"{permission_type}:{category}"
        resource_orders[resource_order_key] = resource_orders.get(resource_order_key, 0) + 1
        payload = _build_resource_group_payload(
            permission_type,
            category,
            resource,
            parent_id=None,
            sort_order=resource_orders[resource_order_key],
        )
        if payload is not None:
            payloads.append(payload)
            seen_resource_groups.add(resource_group_name)

    return payloads


def _build_update_data(existing: Permission, payload: dict[str, Any]) -> dict[str, Any]:
    update_data: dict[str, Any] = {}
    for field in _SYNC_FIELDS:
        if getattr(existing, field) != payload.get(field):
            update_data[field] = payload.get(field)
    if update_data:
        update_data["version"] = existing.version
    return update_data


async def _sync_permission_node(
    repo: PermissionRepository,
    db: AsyncSession,
    payload: dict[str, Any],
    existing_by_name: dict[str, Permission],
    resolved_ids: dict[str, int],
    result: dict[str, int],
    dry_run: bool,
    temp_id_state: dict[str, int],
) -> None:
    existing = existing_by_name.get(payload["name"])
    if existing is None:
        if dry_run:
            resolved_ids[payload["name"]] = temp_id_state["next"]
            temp_id_state["next"] -= 1
        else:
            created = await repo.create(db, payload)
            if created is None:
                return
            existing_by_name[created.name] = created
            if created.id is not None:
                resolved_ids[created.name] = created.id

        result["created"] += 1
        return

    update_data = _build_update_data(existing, payload)
    if not update_data:
        result["skipped"] += 1
        if existing.id is not None:
            resolved_ids[existing.name] = existing.id
        return

    if not dry_run and existing.id is not None:
        _ = await repo.update(db, existing.id, update_data)
        await db.refresh(existing)

    result["updated"] += 1
    if existing.id is not None:
        resolved_ids[existing.name] = existing.id


async def sync_permissions_to_db(
    app: FastAPI,
    db: AsyncSession,
    dry_run: bool = False,
    auto_commit: bool = True,
) -> dict[str, int]:
    """将代码中扫描到的权限同步到数据库"""
    scanned_perms = scan_routes_for_permissions(app)
    result = {"created": 0, "updated": 0, "skipped": 0, "total": len(scanned_perms)}

    if not scanned_perms:
        return result

    repo = PermissionRepository()
    existing_permissions: list[Permission] = list((await db.execute(select(Permission))).scalars().all())
    existing_by_name = {permission.name: permission for permission in existing_permissions}
    resolved_ids = {permission.name: permission.id for permission in existing_permissions if permission.id is not None}
    temp_id_state = {"next": -1}

    for group_payload in _build_group_payloads(scanned_perms):
        if group_payload["resource"] != "system":
            group_payload["parent_id"] = resolved_ids.get(f"{group_payload['category']}:system:group")
        await _sync_permission_node(
            repo,
            db,
            group_payload,
            existing_by_name,
            resolved_ids,
            result,
            dry_run,
            temp_id_state,
        )

    leaf_orders: dict[str, int] = {}
    for permission_data in scanned_perms:
        category = permission_data.get("category")
        resource = permission_data.get("resource")
        if not category or not resource:
            parent_key = "__root__"
            parent_id = None
        elif resource == "system":
            parent_key = f"{category}:system:group"
            parent_id = resolved_ids.get(parent_key)
        else:
            parent_key = f"{category}:{resource}:group"
            parent_id = resolved_ids.get(parent_key)

        leaf_orders[parent_key] = leaf_orders.get(parent_key, 0) + 1
        payload: dict[str, Any] = {
            **permission_data,
            "parent_id": parent_id,
            "sort_order": leaf_orders[parent_key],
        }
        await _sync_permission_node(
            repo,
            db,
            payload,
            existing_by_name,
            resolved_ids,
            result,
            dry_run,
            temp_id_state,
        )

    if auto_commit and not dry_run and (result["created"] > 0 or result["updated"] > 0):
        await db.commit()
        logger.info(
            "自动同步权限完成: 新增 %s 条，更新 %s 条，跳过 %s 条",
            result["created"],
            result["updated"],
            result["skipped"],
        )

    return result


async def sync_builtin_role_permissions(
    db: AsyncSession,
    dry_run: bool = False,
    auto_commit: bool = True,
) -> dict[str, int]:
    """按内置角色规则补齐角色-权限关联"""
    roles: list[Role] = list((await db.execute(select(Role))).scalars().all())
    permissions: list[Permission] = list((await db.execute(select(Permission))).scalars().all())
    existing_links: set[tuple[int, int]] = {
        (int(role_id), int(permission_id))
        for role_id, permission_id in (
            await db.execute(select(role_permission.c.role_id, role_permission.c.permission_id))
        ).all()
    }
    role_by_name = {role.name: role for role in roles}

    result = {"added": 0, "skipped": 0, "roles_processed": 0}
    new_links: list[dict[str, int]] = []

    for role_name, matcher in _ROLE_PERMISSION_RULES.items():
        role = role_by_name.get(role_name)
        if role is None or role.id is None:
            continue

        result["roles_processed"] += 1

        for permission in permissions:
            if permission.id is None or not matcher(permission):
                continue

            link_key = (role.id, permission.id)
            if link_key in existing_links:
                result["skipped"] += 1
                continue

            existing_links.add(link_key)
            new_links.append({"role_id": role.id, "permission_id": permission.id})
            result["added"] += 1

    if new_links and not dry_run:
        _ = await db.execute(role_permission.insert(), new_links)
        if auto_commit:
            await db.commit()
        logger.info("内置角色权限回填完成: 新增 %s 条关联", result["added"])

    return result
