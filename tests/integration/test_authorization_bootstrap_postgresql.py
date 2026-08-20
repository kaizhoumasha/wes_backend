"""授权 bootstrap 在隔离 PostgreSQL/Redis 上的事务与缓存验收。"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Any

import pytest
from fastapi import Depends, FastAPI
from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.app.admin.models import Permission, Role, User, role_permission, user_role
from src.app.admin.repositories.user_repository import UserRepository
from src.app.admin.services import (
    AuthorizationBootstrapService,
    AuthorizationSyncResult,
    BootstrapFoundationConfig,
    PermissionCatalogSyncResult,
)
from src.core.security import get_password_hash
from src.database.redis_cache import RedisCache
from src.database.redis_namespace import database_redis_cache_prefix
from src.register import create_app
from src.utils.permission_scanner import build_permission_catalog
from tests.support.runtime_inbox_postgresql import run_alembic, temporary_database

if TYPE_CHECKING:
    from redis.asyncio import Redis

pytestmark = pytest.mark.integration


def _add_new_current_permission(app: FastAPI) -> None:
    async def require_permission() -> None:
        return None

    require_permission.permission_required = "sys:auditlog:recent"  # type: ignore[attr-defined]
    require_permission.is_rbac = True  # type: ignore[attr-defined]

    async def endpoint() -> None:
        return None

    app.add_api_route(
        "/api/v1/sys/audit-logs/recent",
        endpoint,
        dependencies=[Depends(require_permission)],
        methods=["GET"],
        name="authorization_new_auditlog_read",
    )


async def _count(db: AsyncSession, table: Any) -> int:
    return int(await db.scalar(select(func.count()).select_from(table)) or 0)


class _FailBeforeAdminRoleFlushUserRepository(UserRepository):
    async def ensure_role_link(self, db: AsyncSession, user_id: int, role_id: int) -> bool:
        raise RuntimeError("injected failure before admin role flush")


def test_fresh_bootstrap_rolls_back_atomically_converges_exactly_and_is_idempotent() -> None:
    async def scenario() -> None:
        async with temporary_database() as (_database, database_url):
            run_alembic("upgrade", "head", database_url=database_url)
            engine = create_async_engine(database_url, pool_pre_ping=True)
            session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            app = create_app()
            config = BootstrapFoundationConfig("prod-admin", "StrongPassw0rd!")
            try:
                async with session_factory() as db:
                    service = AuthorizationBootstrapService()
                    fresh_preview = await service.converge_authorization(app, db, dry_run=True)
                    assert fresh_preview.roles == {"created": 5, "updated": 0, "skipped": 0}
                    # Regression: ISSUE-003 — 退役 external callback 后授权快照仍保留旧计数
                    # Found by /qa on 2026-08-21
                    assert fresh_preview.permissions.created == 168
                    assert fresh_preview.role_permissions == {
                        "added": 434,
                        "removed": 0,
                        "skipped": 0,
                        "roles_processed": 5,
                    }
                    assert await _count(db, Role) == 0
                    assert await _count(db, Permission) == 0
                    assert await _count(db, User) == 0
                    assert await _count(db, role_permission) == 0
                    assert await _count(db, user_role) == 0

                    failing_service = AuthorizationBootstrapService(user_repo=_FailBeforeAdminRoleFlushUserRepository())
                    with pytest.raises(RuntimeError, match="injected failure"):
                        await failing_service.bootstrap(app, db, config)
                    await db.rollback()

                    assert await _count(db, Role) == 0
                    assert await _count(db, Permission) == 0
                    assert await _count(db, User) == 0
                    assert await _count(db, role_permission) == 0
                    assert await _count(db, user_role) == 0

                    first = await service.bootstrap(app, db, config)
                    await db.commit()

                    assert first.admin_action == "created"
                    assert first.admin_role_added is True
                    assert first.authorization.role_permissions == fresh_preview.role_permissions
                    roles = list((await db.execute(select(Role).where(Role.is_deleted.is_(False)))).scalars())
                    assert {role.name for role in roles} == {
                        "系统管理员",
                        "管理员",
                        "运营人员",
                        "财务人员",
                        "普通用户",
                    }
                    permissions = list(
                        (await db.execute(select(Permission).where(Permission.is_deleted.is_(False)))).scalars()
                    )
                    expected_permission_names = {payload["name"] for payload in build_permission_catalog(app)}
                    assert {permission.name for permission in permissions} == expected_permission_names

                    role_names_by_id = {role.id: role.name for role in roles}
                    permission_names_by_id = {permission.id: permission.name for permission in permissions}
                    actual_role_permissions: dict[str, set[str]] = {role.name: set() for role in roles}
                    for role_id, permission_id in (await db.execute(select(role_permission))).all():
                        actual_role_permissions[role_names_by_id[role_id]].add(permission_names_by_id[permission_id])
                    assert {role_name: len(names) for role_name, names in actual_role_permissions.items()} == {
                        "系统管理员": 168,
                        "管理员": 51,
                        "运营人员": 106,
                        "财务人员": 3,
                        "普通用户": 106,
                    }
                    assert actual_role_permissions["系统管理员"] == expected_permission_names
                    assert actual_role_permissions["财务人员"] == {
                        "sys:auditlog:group",
                        "sys:auditlog:detail",
                        "sys:auditlog:list",
                    }
                    current_read_permissions = {
                        "admin:menu:list",
                        "admin:menu:siblings",
                        "admin:permission:ancestors",
                        "biz:workline:active-objects",
                        "biz:workline:configuration-status",
                    }
                    assert current_read_permissions <= actual_role_permissions["运营人员"]
                    assert current_read_permissions <= actual_role_permissions["普通用户"]
                    assert "biz:workline:update" not in actual_role_permissions["运营人员"]
                    assert "biz:workline:update" not in actual_role_permissions["普通用户"]

                    permission_count = await _count(db, Permission)
                    role_permission_count = await _count(db, role_permission)
                    _add_new_current_permission(app)
                    new_permission_preview = await service.converge_authorization(app, db, dry_run=True)
                    assert new_permission_preview.permissions.created == 1
                    assert new_permission_preview.role_permissions == {
                        "added": 4,
                        "removed": 0,
                        "skipped": 434,
                        "roles_processed": 5,
                    }
                    assert await _count(db, Permission) == permission_count
                    assert await _count(db, role_permission) == role_permission_count

                    new_permission_result = await service.converge_authorization(app, db, dry_run=False)
                    assert new_permission_result.permissions.created == 1
                    assert new_permission_result.role_permissions == new_permission_preview.role_permissions
                    await db.commit()

                    admin = (await db.execute(select(User).where(User.username == "prod-admin"))).scalar_one()
                    system_role = next(role for role in roles if role.name == "系统管理员")
                    manager_role = next(role for role in roles if role.name == "管理员")
                    assert admin.id is not None
                    assert system_role.id is not None
                    assert manager_role.id is not None
                    assert (
                        await db.execute(
                            select(user_role).where(
                                user_role.c.user_id == admin.id,
                                user_role.c.role_id == system_role.id,
                            )
                        )
                    ).first() is not None

                    changed_member = User(
                        username="changed-role-member",
                        email="changed-role-member@example.com",
                        full_name="Changed Role Member",
                        hashed_password=get_password_hash("StrongPassw0rd!"),
                    )
                    unchanged_member = User(
                        username="unchanged-role-member",
                        email="unchanged-role-member@example.com",
                        full_name="Unchanged Role Member",
                        hashed_password=get_password_hash("StrongPassw0rd!"),
                    )
                    db.add_all([changed_member, unchanged_member])
                    await db.flush()
                    assert changed_member.id is not None
                    assert unchanged_member.id is not None
                    await db.execute(
                        insert(user_role),
                        [
                            {"user_id": changed_member.id, "role_id": manager_role.id},
                            {"user_id": unchanged_member.id, "role_id": system_role.id},
                        ],
                    )
                    missing_permission = next(
                        permission for permission in permissions if permission.name == "admin:user:list"
                    )
                    unexpected_permission = next(
                        permission for permission in permissions if permission.name == "biz:workline:update"
                    )
                    assert missing_permission.id is not None
                    assert unexpected_permission.id is not None
                    await db.execute(
                        delete(role_permission).where(
                            role_permission.c.role_id == manager_role.id,
                            role_permission.c.permission_id == missing_permission.id,
                        )
                    )
                    await db.execute(
                        insert(role_permission).values(
                            role_id=manager_role.id,
                            permission_id=unexpected_permission.id,
                        )
                    )
                    await db.commit()

                    repaired = await service.converge_authorization(app, db, dry_run=False)
                    assert repaired.role_permissions["added"] == 1
                    assert repaired.role_permissions["removed"] == 1
                    assert repaired.affected_user_ids == frozenset({changed_member.id})
                    assert admin.id not in repaired.affected_user_ids
                    assert unchanged_member.id not in repaired.affected_user_ids
                    await db.commit()

                    second = await service.bootstrap(app, db, config)
                    assert second.authorization.roles == {"created": 0, "updated": 0, "skipped": 5}
                    assert second.authorization.permissions.created == 0
                    assert second.authorization.permissions.updated == 0
                    assert second.authorization.permissions.deleted == 0
                    assert second.authorization.role_permissions["added"] == 0
                    assert second.authorization.role_permissions["removed"] == 0
                    assert second.authorization.affected_user_ids == frozenset()
                    assert second.admin_action == "skipped"
                    assert second.admin_role_added is False
                    await db.rollback()
            finally:
                await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.asyncio(loop_scope="session")
async def test_permission_cache_invalidation_and_namespace_repair_use_only_exact_real_redis_keys(
    redis_client: Redis,
) -> None:
    database_identity = f"wes_test_{uuid.uuid4().hex}"
    other_database_identity = f"wes_test_{uuid.uuid4().hex}"
    prefix = database_redis_cache_prefix(database_identity)
    other_prefix = database_redis_cache_prefix(other_database_identity)
    cache = RedisCache(redis_client, prefix=prefix)
    other_database_cache = RedisCache(redis_client, prefix=other_prefix)
    service = AuthorizationBootstrapService()
    shared_logical_key = "role:shared"
    raw_keys = {
        "user": f"{prefix}:perms:user:101",
        "app": f"{prefix}:api_app:perms:202",
        "other": f"{prefix}:unrelated:keep",
    }
    try:
        assert await cache.set(shared_logical_key, {"database": database_identity}) is True
        assert await other_database_cache.get(shared_logical_key) is None
        assert await RedisCache(redis_client, prefix=prefix).get(shared_logical_key) == {"database": database_identity}

        await redis_client.mset(dict.fromkeys(raw_keys.values(), "cached"))
        result = AuthorizationSyncResult(
            roles={"created": 0, "updated": 0, "skipped": 5},
            permissions=PermissionCatalogSyncResult(
                0,
                0,
                0,
                1,
                1,
                affected_app_ids=frozenset({202}),
            ),
            role_permissions={"added": 0, "removed": 0, "skipped": 1, "roles_processed": 5},
            affected_user_ids=frozenset({101}),
        )

        await service.invalidate_caches(result, cache)

        assert await redis_client.exists(raw_keys["user"]) == 0
        assert await redis_client.exists(raw_keys["app"]) == 0
        assert await redis_client.exists(raw_keys["other"]) == 1

        await redis_client.mset(
            {
                raw_keys["user"]: "cached",
                raw_keys["app"]: "cached",
            }
        )
        await service.repair_permission_cache_namespaces(cache)
        await service.repair_permission_cache_namespaces(cache)

        assert await redis_client.exists(raw_keys["user"]) == 0
        assert await redis_client.exists(raw_keys["app"]) == 0
        assert await redis_client.exists(raw_keys["other"]) == 1
    finally:
        keys = [key async for key in redis_client.scan_iter(match=f"{prefix}:*")] + [
            key async for key in redis_client.scan_iter(match=f"{other_prefix}:*")
        ]
        if keys:
            await redis_client.delete(*keys)
