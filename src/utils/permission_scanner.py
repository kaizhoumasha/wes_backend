from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi.routing import APIRoute

from src.core.logger import logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.admin.models import Permission


_READ_ONLY_SUFFIXES = (":list", ":detail", ":tree")
_USER_READ_ONLY_SUFFIXES = (":list", ":detail")
_ROLE_PERMISSION_RULES: dict[str, Callable[[Permission], bool]] = {
    "系统管理员": lambda _permission: True,
    "管理员": lambda permission: permission.name.startswith("admin:"),
    "运营人员": lambda permission: permission.name.endswith(_READ_ONLY_SUFFIXES),
    "财务人员": lambda permission: permission.name.startswith("admin:audit:"),
    "普通用户": lambda permission: permission.name.endswith(_USER_READ_ONLY_SUFFIXES),
}


class PermissionCatalogError(RuntimeError):
    """权限目录不完整或存在歧义。"""


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
        "method": sorted(route.methods)[0] if route.methods else None,
        "path": route.path,
    }


def scan_routes_for_permissions(app: FastAPI) -> list[dict[str, Any]]:
    """扫描 FastAPI 应用中的所有路由，提取权限信息"""
    permissions_found: list[dict[str, Any]] = []

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue

        for dep in route.dependencies:
            dependency_obj = dep.dependency
            perm_name = getattr(dependency_obj, "permission_required", None)
            if not perm_name:
                continue

            is_api_auth = getattr(dependency_obj, "is_api_auth", False)
            is_rbac = getattr(dependency_obj, "is_rbac", False)
            is_superuser = getattr(dependency_obj, "is_superuser", False)

            if is_api_auth:
                permissions_found.append(_build_permission_record(route, perm_name, "app_api"))
            elif is_rbac:
                permissions_found.append(_build_permission_record(route, perm_name, "user_api"))
            elif is_superuser:
                continue

    return sorted(
        permissions_found,
        key=lambda payload: (
            payload["name"],
            payload["type"],
            payload.get("method") or "",
            payload.get("path") or "",
            payload.get("description") or "",
        ),
    )


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

    categories = sorted(
        {
            (permission["type"], permission["category"])
            for permission in scanned_perms
            if permission.get("type") and permission.get("category") and permission.get("resource")
        }
    )
    category_orders: dict[str, int] = {}
    for permission_type, category in categories:
        category_orders[permission_type] = category_orders.get(permission_type, 0) + 1
        payloads.append(
            _build_category_group_payload(permission_type, category, sort_order=category_orders[permission_type])
        )

    resources = sorted(
        {
            (permission["type"], permission["category"], permission["resource"])
            for permission in scanned_perms
            if permission.get("type")
            and permission.get("category")
            and permission.get("resource")
            and permission["resource"] != "system"
        }
    )
    resource_orders: dict[tuple[str, str], int] = {}
    for permission_type, category, resource in resources:
        order_key = (permission_type, category)
        resource_orders[order_key] = resource_orders.get(order_key, 0) + 1
        payload = _build_resource_group_payload(
            permission_type,
            category,
            resource,
            parent_id=None,
            sort_order=resource_orders[order_key],
        )
        if payload is not None:
            payloads.append(payload)

    return payloads


def build_permission_catalog(app: FastAPI) -> list[dict[str, Any]]:
    """构建完整、确定且无名称歧义的权限目录。"""
    scanned_permissions = scan_routes_for_permissions(app)
    if not scanned_permissions:
        raise PermissionCatalogError("未扫描到权限")

    leaves_by_name: dict[str, dict[str, Any]] = {}
    for permission in scanned_permissions:
        existing = leaves_by_name.get(permission["name"])
        if existing is not None and existing != permission:
            existing_tuple = (existing["type"], existing.get("method"), existing.get("path"))
            permission_tuple = (permission["type"], permission.get("method"), permission.get("path"))
            raise PermissionCatalogError(
                f"重复权限码 `{permission['name']}` 指向不同定义: {existing_tuple!r} != {permission_tuple!r}"
            )
        leaves_by_name[permission["name"]] = permission

    leaf_orders: dict[str, int] = {}
    leaf_payloads: list[dict[str, Any]] = []
    for permission in sorted(
        leaves_by_name.values(),
        key=lambda payload: (
            payload["type"],
            payload.get("category") or "",
            payload.get("resource") or "",
            payload["name"],
            payload.get("method") or "",
            payload.get("path") or "",
        ),
    ):
        category = permission.get("category")
        resource = permission.get("resource")
        if not category or not resource:
            parent_name = "__root__"
        elif resource == "system":
            parent_name = f"{category}:system:group"
        else:
            parent_name = f"{category}:{resource}:group"
        leaf_orders[parent_name] = leaf_orders.get(parent_name, 0) + 1
        leaf_payloads.append(
            {
                **permission,
                "parent_id": None,
                "sort_order": leaf_orders[parent_name],
            }
        )

    catalog: list[dict[str, Any]] = []
    payloads_by_name: dict[str, dict[str, Any]] = {}
    group_payloads = _build_group_payloads(list(leaves_by_name.values()))
    colliding_names = sorted(leaves_by_name.keys() & {payload["name"] for payload in group_payloads})
    if colliding_names:
        raise PermissionCatalogError(f"权限目录名称冲突: 叶子权限与派生分组同名 `{colliding_names[0]}`")

    for payload in [*group_payloads, *leaf_payloads]:
        existing = payloads_by_name.get(payload["name"])
        if existing is not None and existing != payload:
            raise PermissionCatalogError(f"权限目录名称冲突: `{payload['name']}` 对应不同 payload")
        if existing is None:
            payloads_by_name[payload["name"]] = payload
            catalog.append(payload)

    return catalog


def managed_permission_names_for_app(app: FastAPI) -> set[str]:
    """返回当前路由扫描器拥有的叶子权限和分组权限名称。"""
    return {payload["name"] for payload in build_permission_catalog(app)}


async def sync_builtin_role_permissions(
    db: AsyncSession,
    dry_run: bool = False,
    auto_commit: bool = True,
    *,
    exact: bool = False,
    managed_permission_names: set[str] | None = None,
) -> dict[str, int]:
    """按内置角色规则同步角色-权限关联；通用入口默认只增不删。"""
    from sqlalchemy import delete, select, tuple_

    from src.app.admin.models import Permission, Role, role_permission

    roles: list[Role] = list((await db.execute(select(Role).where(Role.is_deleted.is_(False)))).scalars().all())
    permissions: list[Permission] = list(
        (await db.execute(select(Permission).where(Permission.is_deleted.is_(False)))).scalars().all()
    )
    if managed_permission_names is not None:
        permissions = [permission for permission in permissions if permission.name in managed_permission_names]
    existing_links: set[tuple[int, int]] = {
        (int(role_id), int(permission_id))
        for role_id, permission_id in (
            await db.execute(select(role_permission.c.role_id, role_permission.c.permission_id))
        ).all()
    }
    role_by_name = {role.name: role for role in roles}

    result = {"added": 0, "removed": 0, "skipped": 0, "roles_processed": 0}
    new_links: list[dict[str, int]] = []
    expected_links: set[tuple[int, int]] = set()

    for role_name, matcher in _ROLE_PERMISSION_RULES.items():
        role = role_by_name.get(role_name)
        if role is None or role.id is None:
            continue

        result["roles_processed"] += 1

        for permission in permissions:
            if permission.id is None or not matcher(permission):
                continue

            link_key = (role.id, permission.id)
            expected_links.add(link_key)
            if link_key in existing_links:
                result["skipped"] += 1
                continue

            existing_links.add(link_key)
            new_links.append({"role_id": role.id, "permission_id": permission.id})
            result["added"] += 1

    extra_links: set[tuple[int, int]] = set()
    if exact:
        builtin_role_ids = {
            role.id
            for role_name, role in role_by_name.items()
            if role_name in _ROLE_PERMISSION_RULES and role.id is not None
        }
        extra_links = {link for link in existing_links if link[0] in builtin_role_ids and link not in expected_links}
    result["removed"] = len(extra_links)

    if new_links and not dry_run:
        _ = await db.execute(role_permission.insert(), new_links)
    if extra_links and not dry_run:
        _ = await db.execute(
            delete(role_permission).where(
                tuple_(role_permission.c.role_id, role_permission.c.permission_id).in_(extra_links)
            )
        )
    if auto_commit and not dry_run and (new_links or extra_links):
        await db.commit()
        logger.info("内置角色权限收敛完成: 新增 %s 条，移除 %s 条", result["added"], result["removed"])

    return result
