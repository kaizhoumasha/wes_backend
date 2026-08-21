"""内置授权目录与首个管理员的唯一基础收敛服务。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.app.admin.repositories.perm_repository import PermissionRepository, permission_repository
from src.app.admin.repositories.role_repository import RoleRepository, role_repository
from src.app.admin.repositories.user_repository import UserRepository, user_repository
from src.app.admin.services.permission_catalog_service import (
    PermissionCatalogService,
    PermissionCatalogSyncResult,
    permission_catalog_service,
)
from src.core.authorization_cache import (
    AuthorizationCacheInvalidationError,
    repair_permission_cache_namespaces,
)
from src.core.rbac import invalidate_users_permissions
from src.core.security import get_password_hash
from src.utils.permission_scanner import build_permission_catalog

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.admin.models import Role, User
    from src.database.redis_cache import RedisCache


@dataclass(frozen=True, slots=True)
class _BuiltinRoleSpec:
    name: str
    description: str
    matches_permission: Callable[[_PolicyPermission], bool]


@dataclass(frozen=True, slots=True)
class _PolicyPermission:
    id: int | None
    name: str
    category: str | None
    resource: str | None
    action: str | None
    method: str | None
    path: str | None


def _is_semantic_read(permission: _PolicyPermission) -> bool:
    return permission.method == "GET" or (
        permission.method == "POST"
        and permission.action == "list"
        and (permission.path or "").rstrip("/").endswith("/query")
    )


def _is_admin_permission(permission: _PolicyPermission) -> bool:
    return permission.category == "admin"


def _is_finance_permission(permission: _PolicyPermission) -> bool:
    return permission.category == "sys" and permission.resource == "auditlog"


BUILTIN_ROLE_SPECS = (
    _BuiltinRoleSpec("系统管理员", "系统最高权限，拥有所有操作权限", lambda _permission: True),
    _BuiltinRoleSpec("管理员", "系统管理员，拥有大部分管理权限", _is_admin_permission),
    _BuiltinRoleSpec("运营人员", "日常运营操作人员", _is_semantic_read),
    _BuiltinRoleSpec("财务人员", "财务相关操作人员", _is_finance_permission),
    _BuiltinRoleSpec("普通用户", "普通用户，基础查看权限", _is_semantic_read),
)

_CACHE_INVALIDATION_ATTEMPTS = 3
_CACHE_INVALIDATION_BACKOFF_SECONDS = 0.05


@dataclass(frozen=True, slots=True)
class BootstrapFoundationConfig:
    username: str
    password: str
    full_name: str | None = None
    email: str | None = None


@dataclass(frozen=True, slots=True)
class AuthorizationSyncResult:
    roles: dict[str, int]
    permissions: PermissionCatalogSyncResult
    role_permissions: dict[str, int]
    affected_user_ids: frozenset[int]


@dataclass(frozen=True, slots=True)
class FoundationBootstrapResult:
    authorization: AuthorizationSyncResult
    admin_action: str
    admin_username: str
    admin_role_added: bool


class AuthorizationBootstrapService:
    """精确收敛五个内置角色、权限关系与首个超级管理员。"""

    def __init__(
        self,
        *,
        catalog_service: PermissionCatalogService = permission_catalog_service,
        permission_repo: PermissionRepository = permission_repository,
        role_repo: RoleRepository = role_repository,
        user_repo: UserRepository = user_repository,
    ) -> None:
        self.catalog_service = catalog_service
        self.permission_repo = permission_repo
        self.role_repo = role_repo
        self.user_repo = user_repo

    async def _converge_builtin_roles(
        self,
        db: AsyncSession,
        *,
        dry_run: bool,
    ) -> tuple[dict[str, int], dict[str, Role]]:
        role_names = {spec.name for spec in BUILTIN_ROLE_SPECS}
        roles = await self.role_repo.get_active_by_names(db, role_names)
        result = {"created": 0, "updated": 0, "skipped": 0}

        for spec in BUILTIN_ROLE_SPECS:
            role = roles.get(spec.name)
            if role is None:
                result["created"] += 1
                if dry_run:
                    continue
                created = await self.role_repo.create(db, {"name": spec.name, "description": spec.description})
                if created is None or created.id is None:
                    raise RuntimeError(f"创建内置角色失败: {spec.name}")
                roles[spec.name] = created
                continue

            if role.description == spec.description:
                result["skipped"] += 1
                continue

            result["updated"] += 1
            if dry_run:
                continue
            updated = await self.role_repo.update(
                db,
                role.id,
                {"description": spec.description, "version": role.version},
            )
            if updated is None:
                raise RuntimeError(f"修复内置角色描述失败: {spec.name}")
            roles[spec.name] = updated

        return result, roles

    async def _converge_builtin_role_permissions(
        self,
        db: AsyncSession,
        roles: dict[str, Role],
        *,
        dry_run: bool,
        desired_permissions: tuple[_PolicyPermission, ...] | None = None,
    ) -> tuple[dict[str, int], set[int]]:
        persisted_permissions = [
            _PolicyPermission(
                id=permission.id,
                name=permission.name,
                category=permission.category,
                resource=permission.resource,
                action=permission.action,
                method=permission.method,
                path=permission.path,
            )
            for permission in await self.permission_repo.list_catalog_nodes(db)
            if not permission.is_deleted and permission.id is not None
        ]
        desired = desired_permissions or tuple(persisted_permissions)
        desired_by_name = {permission.name: permission for permission in desired}
        persisted_by_name = {
            permission.name: permission for permission in persisted_permissions if permission.name in desired_by_name
        }
        role_ids = {role.id for role in roles.values() if role.id is not None}
        current_by_role = await self.role_repo.get_permission_ids_by_role_ids(db, role_ids)
        affected_user_ids: set[int] = set()
        result = {"added": 0, "removed": 0, "skipped": 0, "roles_processed": 0}

        for spec in BUILTIN_ROLE_SPECS:
            role = roles.get(spec.name)
            result["roles_processed"] += 1
            expected_names = {
                permission.name for permission in desired_by_name.values() if spec.matches_permission(permission)
            }
            current_ids = current_by_role.get(role.id, set()) if role is not None and role.id is not None else set()
            current_names = {
                name
                for name, permission in persisted_by_name.items()
                if permission.id is not None and permission.id in current_ids
            }
            added_names = expected_names - current_names
            removed_names = current_names - expected_names
            result["added"] += len(added_names)
            result["removed"] += len(removed_names)
            result["skipped"] += len(expected_names & current_names)

            if not added_names and not removed_names:
                continue
            if role is None or role.id is None:
                continue
            affected_user_ids.update(await self.role_repo.get_user_ids_by_role_id(db, role.id))
            if not dry_run:
                added_ids = {
                    permission.id
                    for name in added_names
                    if (permission := persisted_by_name.get(name)) is not None and permission.id is not None
                }
                if len(added_ids) != len(added_names):
                    raise RuntimeError(f"内置角色权限尚未持久化: {spec.name}")
                removed_ids = {
                    permission.id
                    for name in removed_names
                    if (permission := persisted_by_name.get(name)) is not None and permission.id is not None
                }
                await self.role_repo.apply_permission_delta(db, role.id, added_ids, removed_ids)

        return result, affected_user_ids

    async def converge_authorization(
        self,
        app: FastAPI,
        db: AsyncSession,
        *,
        dry_run: bool = False,
    ) -> AuthorizationSyncResult:
        permission_result = await self.catalog_service.sync(app, db, dry_run=dry_run)
        desired_permissions: tuple[_PolicyPermission, ...] | None = None
        if dry_run:
            desired_permissions = tuple(
                _PolicyPermission(
                    id=None,
                    name=payload["name"],
                    category=payload.get("category"),
                    resource=payload.get("resource"),
                    action=payload.get("action"),
                    method=payload.get("method"),
                    path=payload.get("path"),
                )
                for payload in build_permission_catalog(app)
            )
        roles_result, roles = await self._converge_builtin_roles(db, dry_run=dry_run)
        role_permission_result, role_user_ids = await self._converge_builtin_role_permissions(
            db,
            roles,
            dry_run=dry_run,
            desired_permissions=desired_permissions,
        )
        return AuthorizationSyncResult(
            roles=roles_result,
            permissions=permission_result,
            role_permissions=role_permission_result,
            affected_user_ids=frozenset(permission_result.affected_user_ids | role_user_ids),
        )

    async def ensure_first_superuser(
        self,
        db: AsyncSession,
        config: BootstrapFoundationConfig,
    ) -> tuple[User, str]:
        existing = await self.user_repo.get_first_superuser(db)
        if existing is not None:
            return existing, "skipped"

        username = config.username.strip()
        full_name = (config.full_name or "").strip() or username
        email = (config.email or "").strip().lower() or f"{username}@bootstrap.localdomain"
        created = await self.user_repo.create(
            db,
            {
                "username": username,
                "email": email,
                "full_name": full_name,
                "hashed_password": get_password_hash(config.password),
                "is_superuser": True,
                "is_multi_login": True,
            },
        )
        if created is None or created.id is None:
            raise RuntimeError("创建首个超级管理员失败")
        return created, "created"

    async def ensure_system_admin_role(self, db: AsyncSession, admin: User) -> bool:
        roles = await self.role_repo.get_active_by_names(db, {"系统管理员"})
        role = roles.get("系统管理员")
        if role is None or role.id is None or admin.id is None:
            raise RuntimeError("系统管理员角色或超级管理员尚未持久化")
        return await self.user_repo.ensure_role_link(db, admin.id, role.id)

    async def bootstrap(
        self,
        app: FastAPI,
        db: AsyncSession,
        config: BootstrapFoundationConfig,
    ) -> FoundationBootstrapResult:
        authorization = await self.converge_authorization(app, db, dry_run=False)
        admin, admin_action = await self.ensure_first_superuser(db, config)
        admin_role_added = await self.ensure_system_admin_role(db, admin)
        if admin_role_added and admin.id is not None:
            authorization = AuthorizationSyncResult(
                roles=authorization.roles,
                permissions=authorization.permissions,
                role_permissions=authorization.role_permissions,
                affected_user_ids=frozenset({*authorization.affected_user_ids, admin.id}),
            )
        return FoundationBootstrapResult(
            authorization=authorization,
            admin_action=admin_action,
            admin_username=admin.username,
            admin_role_added=admin_role_added,
        )

    async def invalidate_caches(self, result: AuthorizationSyncResult, cache: RedisCache) -> None:
        from src.app.api_auth.services.permission_service import invalidate_app_permissions

        pending_user_ids = set(result.affected_user_ids)
        pending_app_ids = set(result.permissions.affected_app_ids)

        for attempt in range(_CACHE_INVALIDATION_ATTEMPTS):
            user_results = await invalidate_users_permissions(cache, pending_user_ids)
            pending_user_ids = {user_id for user_id, deleted in user_results.items() if deleted is not True}

            failed_app_ids: set[int] = set()
            for app_id in sorted(pending_app_ids):
                if await invalidate_app_permissions(cache, app_id) is not True:
                    failed_app_ids.add(app_id)
            pending_app_ids = failed_app_ids

            if not pending_user_ids and not pending_app_ids:
                return
            if attempt + 1 < _CACHE_INVALIDATION_ATTEMPTS:
                await asyncio.sleep(_CACHE_INVALIDATION_BACKOFF_SECONDS)

        raise AuthorizationCacheInvalidationError(
            failed_user_ids=frozenset(pending_user_ids),
            failed_app_ids=frozenset(pending_app_ids),
        )

    async def repair_permission_cache_namespaces(self, cache: RedisCache) -> None:
        await repair_permission_cache_namespaces(cache)


authorization_bootstrap_service = AuthorizationBootstrapService()

__all__ = [
    "BUILTIN_ROLE_SPECS",
    "AuthorizationBootstrapService",
    "AuthorizationCacheInvalidationError",
    "AuthorizationSyncResult",
    "BootstrapFoundationConfig",
    "FoundationBootstrapResult",
    "authorization_bootstrap_service",
]
