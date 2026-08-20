from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.app.admin.services.authorization_bootstrap_service import (
    BUILTIN_ROLE_SPECS,
    AuthorizationBootstrapService,
    AuthorizationCacheInvalidationError,
    AuthorizationSyncResult,
    BootstrapFoundationConfig,
)
from src.app.admin.services.permission_catalog_service import PermissionCatalogSyncResult


class _CatalogService:
    def __init__(self, result: PermissionCatalogSyncResult | None = None) -> None:
        self.result = result or PermissionCatalogSyncResult(0, 0, 0, 4, 4)
        self.calls: list[tuple[object, object, bool]] = []

    async def sync(self, app: object, db: object, *, dry_run: bool) -> PermissionCatalogSyncResult:
        self.calls.append((app, db, dry_run))
        return self.result


class _PermissionRepository:
    def __init__(self) -> None:
        self.nodes = [
            SimpleNamespace(id=11, name="admin:user:list", is_deleted=False),
            SimpleNamespace(id=12, name="admin:audit:export", is_deleted=False),
            SimpleNamespace(id=13, name="biz:order:detail", is_deleted=False),
            SimpleNamespace(id=14, name="biz:order:update", is_deleted=False),
        ]

    async def list_catalog_nodes(self, db: object) -> list[object]:
        return list(self.nodes)


class _RoleRepository:
    def __init__(self, roles: list[object] | None = None) -> None:
        self.roles = {role.name: role for role in roles or []}
        self.permission_ids: dict[int, set[int]] = {
            int(role.id): set(getattr(role, "permission_ids", set())) for role in roles or []
        }
        self.user_ids: dict[int, set[int]] = {
            int(role.id): set(getattr(role, "user_ids", set())) for role in roles or []
        }
        self.next_id = max((int(role.id) for role in roles or []), default=100) + 1

    async def get_active_by_names(self, db: object, names: set[str]) -> dict[str, object]:
        return {name: role for name, role in self.roles.items() if name in names}

    async def create(self, db: object, data: dict[str, Any]) -> object:
        role = SimpleNamespace(id=self.next_id, version=0, **data)
        self.next_id += 1
        self.roles[role.name] = role
        self.permission_ids[role.id] = set()
        self.user_ids[role.id] = set()
        return role

    async def update(self, db: object, role_id: int, data: dict[str, Any]) -> object:
        role = next(role for role in self.roles.values() if role.id == role_id)
        role.description = data["description"]
        role.version += 1
        return role

    async def get_permission_ids_by_role_ids(self, db: object, role_ids: set[int]) -> dict[int, set[int]]:
        return {role_id: set(self.permission_ids.get(role_id, set())) for role_id in role_ids}

    async def apply_permission_delta(
        self,
        db: object,
        role_id: int,
        added_permission_ids: set[int],
        removed_permission_ids: set[int],
    ) -> None:
        self.permission_ids[role_id].difference_update(removed_permission_ids)
        self.permission_ids[role_id].update(added_permission_ids)

    async def get_user_ids_by_role_id(self, db: object, role_id: int) -> set[int]:
        return set(self.user_ids.get(role_id, set()))


class _UserRepository:
    def __init__(self, superuser: object | None = None) -> None:
        self.superuser = superuser
        self.created_payloads: list[dict[str, Any]] = []
        self.role_links: set[tuple[int, int]] = set()

    async def get_first_superuser(self, db: object) -> object | None:
        return self.superuser

    async def create(self, db: object, data: dict[str, Any]) -> object:
        self.created_payloads.append(data)
        self.superuser = SimpleNamespace(id=501, username=data["username"])
        return self.superuser

    async def ensure_role_link(self, db: object, user_id: int, role_id: int) -> bool:
        link = (user_id, role_id)
        if link in self.role_links:
            return False
        self.role_links.add(link)
        return True


def _role(
    role_id: int,
    name: str,
    description: str,
    *,
    permission_ids: set[int] | None = None,
    user_ids: set[int] | None = None,
) -> object:
    return SimpleNamespace(
        id=role_id,
        name=name,
        description=description,
        version=0,
        permission_ids=permission_ids or set(),
        user_ids=user_ids or set(),
    )


def _service(
    *,
    roles: list[object] | None = None,
    users: _UserRepository | None = None,
    catalog_result: PermissionCatalogSyncResult | None = None,
) -> tuple[AuthorizationBootstrapService, _RoleRepository, _UserRepository]:
    role_repo = _RoleRepository(roles)
    user_repo = users or _UserRepository()
    service = AuthorizationBootstrapService(
        catalog_service=_CatalogService(catalog_result),
        permission_repo=_PermissionRepository(),
        role_repo=role_repo,
        user_repo=user_repo,
    )
    return service, role_repo, user_repo


@pytest.mark.asyncio
async def test_converge_creates_exact_builtin_roles_and_preserves_custom_roles() -> None:
    custom = _role(99, "现场自定义角色", "保留", permission_ids={14}, user_ids={900})
    service, role_repo, _users = _service(roles=[custom])
    db = SimpleNamespace(commit=AsyncMock())

    result = await service.converge_authorization(object(), db, dry_run=False)

    assert {spec.name for spec in BUILTIN_ROLE_SPECS} == {
        "系统管理员",
        "管理员",
        "运营人员",
        "财务人员",
        "普通用户",
    }
    assert result.roles == {"created": 5, "updated": 0, "skipped": 0}
    assert role_repo.roles["现场自定义角色"] is custom
    assert role_repo.permission_ids[99] == {14}
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_converge_repairs_descriptions_tracks_only_changed_role_members_and_is_idempotent() -> None:
    roles = [
        _role(1, "系统管理员", "错误描述", permission_ids={11, 12, 13}, user_ids={101, 102}),
        _role(2, "管理员", "系统管理员，拥有大部分管理权限", permission_ids={11, 12, 14}, user_ids={201}),
        _role(3, "运营人员", "日常运营操作人员", permission_ids={11, 13}, user_ids={301}),
        _role(4, "财务人员", "财务相关操作人员", permission_ids={12}),
        _role(5, "普通用户", "普通用户，基础查看权限", permission_ids={11, 13}),
        _role(99, "现场自定义角色", "保留", permission_ids={14}, user_ids={900}),
    ]
    catalog_result = PermissionCatalogSyncResult(0, 0, 0, 4, 4, affected_user_ids=frozenset({77}))
    service, role_repo, _users = _service(roles=roles, catalog_result=catalog_result)

    first = await service.converge_authorization(object(), object(), dry_run=False)

    assert first.roles == {"created": 0, "updated": 1, "skipped": 4}
    assert first.role_permissions == {"added": 1, "removed": 1, "skipped": 10, "roles_processed": 5}
    assert first.affected_user_ids == frozenset({77, 101, 102, 201})
    assert role_repo.permission_ids[1] == {11, 12, 13, 14}
    assert role_repo.permission_ids[2] == {11, 12}
    assert role_repo.permission_ids[99] == {14}

    second = await service.converge_authorization(object(), object(), dry_run=False)

    assert second.roles == {"created": 0, "updated": 0, "skipped": 5}
    assert second.role_permissions["added"] == 0
    assert second.role_permissions["removed"] == 0
    assert second.affected_user_ids == frozenset({77})


@pytest.mark.asyncio
async def test_bootstrap_reuses_superuser_adds_only_missing_system_role_and_is_idempotent() -> None:
    system_role = _role(1, "系统管理员", "系统最高权限，拥有所有操作权限", permission_ids={11, 12, 13, 14})
    existing_admin = SimpleNamespace(id=88, username="existing-admin")
    users = _UserRepository(existing_admin)
    users.role_links.add((88, 99))
    service, _roles, _users = _service(roles=[system_role], users=users)
    config = BootstrapFoundationConfig("ignored", "StrongPassw0rd!")

    first = await service.bootstrap(object(), object(), config)
    second = await service.bootstrap(object(), object(), config)

    assert first.admin_action == "skipped"
    assert first.admin_username == "existing-admin"
    assert first.admin_role_added is True
    assert first.authorization.affected_user_ids == frozenset({88})
    assert second.admin_role_added is False
    assert second.authorization.affected_user_ids == frozenset()
    assert users.created_payloads == []
    assert users.role_links == {(88, 99), (88, 1)}


@pytest.mark.asyncio
async def test_bootstrap_creates_first_superuser_with_normalized_defaults() -> None:
    service, _roles, users = _service()

    result = await service.bootstrap(
        object(),
        object(),
        BootstrapFoundationConfig(username="  prod-admin  ", password="StrongPassw0rd!"),
    )

    assert result.admin_action == "created"
    assert result.admin_username == "prod-admin"
    assert result.admin_role_added is True
    payload = users.created_payloads[0]
    assert payload["email"] == "prod-admin@bootstrap.localdomain"
    assert payload["full_name"] == "prod-admin"
    assert payload["is_superuser"] is True
    assert payload["is_multi_login"] is True
    assert payload["hashed_password"] != "StrongPassw0rd!"


class _OutcomeCache:
    def __init__(self, outcomes: dict[str, list[bool | None]]) -> None:
        self.outcomes = {key: list(values) for key, values in outcomes.items()}
        self.deleted_keys: list[str] = []
        self.deleted_patterns: list[str] = []

    async def delete(self, key: str) -> bool | None:
        self.deleted_keys.append(key)
        values = self.outcomes.setdefault(key, [True])
        return values.pop(0) if len(values) > 1 else values[0]

    async def delete_pattern(self, pattern: str) -> int | None:
        self.deleted_patterns.append(pattern)
        value = await self.delete(f"pattern:{pattern}")
        return 0 if value is True else None


def _sync_result(*, user_ids: set[int], app_ids: set[int]) -> AuthorizationSyncResult:
    return AuthorizationSyncResult(
        roles={"created": 0, "updated": 0, "skipped": 5},
        permissions=PermissionCatalogSyncResult(0, 0, 0, 4, 4, affected_app_ids=frozenset(app_ids)),
        role_permissions={"added": 0, "removed": 0, "skipped": 10, "roles_processed": 5},
        affected_user_ids=frozenset(user_ids),
    )


@pytest.mark.asyncio
async def test_cache_invalidation_retries_only_failed_ids_for_three_total_attempts() -> None:
    service, _roles, _users = _service()
    cache = _OutcomeCache(
        {
            "perms:user:1": [True],
            "perms:user:2": [False, False, True],
            "api_app:perms:3": [False, True],
        }
    )
    await service.invalidate_caches(_sync_result(user_ids={1, 2}, app_ids={3}), cache)

    assert cache.deleted_keys == [
        "perms:user:1",
        "perms:user:2",
        "api_app:perms:3",
        "perms:user:2",
        "api_app:perms:3",
        "perms:user:2",
    ]


@pytest.mark.asyncio
async def test_cache_invalidation_raises_with_ids_remaining_after_three_attempts() -> None:
    service, _roles, _users = _service()
    cache = _OutcomeCache({"perms:user:4": [False, False, False], "api_app:perms:5": [True]})
    with pytest.raises(AuthorizationCacheInvalidationError) as exc_info:
        await service.invalidate_caches(_sync_result(user_ids={4}, app_ids={5}), cache)

    assert exc_info.value.failed_user_ids == frozenset({4})
    assert exc_info.value.failed_app_ids == frozenset()
    assert cache.deleted_keys.count("perms:user:4") == 3
    assert cache.deleted_keys.count("api_app:perms:5") == 1


@pytest.mark.asyncio
async def test_repair_cache_namespaces_accepts_zero_deletions_and_rejects_unconfirmed_namespace() -> None:
    service, _roles, _users = _service()
    success_cache = _OutcomeCache({"pattern:perms:user:*": [True], "pattern:api_app:perms:*": [True]})

    await service.repair_permission_cache_namespaces(success_cache)

    assert success_cache.deleted_patterns == ["perms:user:*", "api_app:perms:*"]

    failed_cache = _OutcomeCache({"pattern:perms:user:*": [True], "pattern:api_app:perms:*": [None]})
    with pytest.raises(AuthorizationCacheInvalidationError, match="api_app:perms"):
        await service.repair_permission_cache_namespaces(failed_cache)
