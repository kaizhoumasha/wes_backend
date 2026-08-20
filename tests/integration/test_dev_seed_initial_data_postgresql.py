"""本机开发基础数据在隔离 PostgreSQL 临时库上的收敛验收。"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from scripts.data.seed_initial_data import (
    _builtin_role_user_ids,
    _check_foundation_data,
    _seed_foundation_data,
)
from src.app.admin.models import Menu, Permission, Role, User, role_menu, role_permission, user_role
from src.app.admin.services.menu_sync_service import menu_sync_service
from src.core.security import get_password_hash
from src.utils.frontend_menu_parser import FrontendMenuDefinition, resolve_frontend_root
from tests.support.runtime_inbox_postgresql import run_alembic, temporary_database

FRONTEND_ROOT = resolve_frontend_root()
SEED_PASSWORD = "admin123"


async def _row_counts(db: AsyncSession) -> tuple[int, ...]:
    tables = (Role, User, Permission, Menu, user_role, role_permission, role_menu)
    counts: list[int] = []
    for table in tables:
        counts.append(int(await db.scalar(select(func.count()).select_from(table)) or 0))
    return tuple(counts)


@pytest.mark.integration
def test_dev_seed_is_idempotent_repairs_drift_and_check_is_read_only() -> None:
    async def scenario() -> None:
        async with temporary_database() as (_database, database_url):
            run_alembic("upgrade", "head", database_url=database_url)
            engine = create_async_engine(database_url, pool_pre_ping=True)
            session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            try:
                async with session_factory() as db:
                    broken_menu = FrontendMenuDefinition(
                        name="broken:child:menu",
                        title="Broken",
                        path="/broken",
                        component=None,
                        sort_order=1,
                        parent_name="missing:parent:menu",
                    )
                    empty_counts = await _row_counts(db)
                    with (
                        patch.object(
                            menu_sync_service,
                            "load_frontend_menu_definitions",
                            return_value=[broken_menu],
                        ),
                        pytest.raises(RuntimeError, match="前端菜单同步失败"),
                    ):
                        await _seed_foundation_data(db, str(FRONTEND_ROOT), SEED_PASSWORD)
                    assert await _row_counts(db) == empty_counts

                    await _seed_foundation_data(db, str(FRONTEND_ROOT), SEED_PASSWORD)
                    first_counts = await _row_counts(db)

                    await _seed_foundation_data(db, str(FRONTEND_ROOT), SEED_PASSWORD)
                    assert await _row_counts(db) == first_counts

                    with (
                        patch(
                            "scripts.data.seed_initial_data.managed_permission_names_for_app",
                            return_value=set(),
                        ),
                        pytest.raises(RuntimeError, match="权限定义为空"),
                    ):
                        await _seed_foundation_data(db, str(FRONTEND_ROOT), SEED_PASSWORD)
                    assert await _row_counts(db) == first_counts

                    active_permission = (
                        await db.execute(
                            select(Permission)
                            .where(Permission.is_deleted.is_(False), Permission.action != "group")
                            .limit(1)
                        )
                    ).scalar_one()
                    active_menu = (
                        await db.execute(select(Menu).where(Menu.is_deleted.is_(False)).limit(1))
                    ).scalar_one()
                    retired_permission_id = active_permission.id
                    retired_menu_id = active_menu.id
                    active_permission.is_deleted = True
                    active_menu.is_deleted = True
                    await db.commit()

                    await _seed_foundation_data(db, str(FRONTEND_ROOT), SEED_PASSWORD)
                    rebuilt_permission = (
                        await db.execute(
                            select(Permission).where(
                                Permission.name == active_permission.name,
                                Permission.is_deleted.is_(False),
                            )
                        )
                    ).scalar_one()
                    rebuilt_menu = (
                        await db.execute(select(Menu).where(Menu.name == active_menu.name, Menu.is_deleted.is_(False)))
                    ).scalar_one()
                    assert rebuilt_permission.id != retired_permission_id
                    assert rebuilt_menu.id != retired_menu_id
                    await _check_foundation_data(db, str(FRONTEND_ROOT), SEED_PASSWORD)
                    first_counts = await _row_counts(db)

                    manager = (await db.execute(select(User).where(User.username == "manager"))).scalar_one()
                    manager_role = (await db.execute(select(Role).where(Role.name == "管理员"))).scalar_one()
                    system_role = (await db.execute(select(Role).where(Role.name == "系统管理员"))).scalar_one()
                    assert manager.id is not None
                    assert manager_role.id is not None
                    assert system_role.id is not None

                    await db.execute(insert(user_role).values(user_id=manager.id, role_id=system_role.id))

                    extra_permission = (
                        await db.execute(select(Permission).where(~Permission.name.startswith("admin:")).limit(1))
                    ).scalar_one()
                    assert extra_permission.id is not None
                    await db.execute(
                        insert(role_permission).values(role_id=manager_role.id, permission_id=extra_permission.id)
                    )

                    extra_menu = (
                        await db.execute(
                            select(Menu)
                            .where(~Menu.name.startswith("admin:"), Menu.name != "system:dashboard:menu")
                            .limit(1)
                        )
                    ).scalar_one()
                    assert extra_menu.id is not None
                    await db.execute(insert(role_menu).values(role_id=manager_role.id, menu_id=extra_menu.id))

                    retired_permission = Permission(
                        name="admin:retired:list",
                        description="retired route",
                        type="user_api",
                        category="admin",
                        resource="retired",
                        action="list",
                        method="GET",
                        path="/retired",
                        sort_order=999,
                    )
                    retired_menu = Menu(
                        name="admin:retired:menu",
                        title="Retired",
                        path="/retired",
                        component=None,
                        sort_order=999,
                    )
                    custom_user = User(
                        username="custom-dev-user",
                        email="custom-dev-user@localhost.localdomain",
                        full_name="Custom Dev User",
                        hashed_password=get_password_hash(SEED_PASSWORD),
                    )
                    db.add_all([retired_permission, retired_menu, custom_user])
                    await db.flush()
                    assert retired_permission.id is not None
                    assert retired_menu.id is not None
                    assert custom_user.id is not None
                    await db.execute(
                        insert(role_permission).values(
                            role_id=manager_role.id,
                            permission_id=retired_permission.id,
                        )
                    )
                    await db.execute(insert(role_menu).values(role_id=manager_role.id, menu_id=retired_menu.id))
                    await db.execute(insert(user_role).values(user_id=custom_user.id, role_id=manager_role.id))
                    await db.commit()

                    drift_counts = await _row_counts(db)
                    assert (
                        await menu_sync_service.sync_builtin_role_menus(db, dry_run=True, auto_commit=False)
                    ).removed == 0
                    with pytest.raises(RuntimeError, match="未收敛"):
                        await _check_foundation_data(db, str(FRONTEND_ROOT), SEED_PASSWORD)
                    assert await _row_counts(db) == drift_counts

                    await _seed_foundation_data(db, str(FRONTEND_ROOT), SEED_PASSWORD)
                    await _check_foundation_data(db, str(FRONTEND_ROOT), SEED_PASSWORD)

                    manager_role_ids = set(
                        (
                            await db.execute(select(user_role.c.role_id).where(user_role.c.user_id == manager.id))
                        ).scalars()
                    )
                    assert manager_role_ids == {manager_role.id}
                    assert custom_user.id in await _builtin_role_user_ids(db)
                    assert not await db.scalar(
                        select(role_permission).where(
                            role_permission.c.role_id == manager_role.id,
                            role_permission.c.permission_id == retired_permission.id,
                        )
                    )
                    assert not await db.scalar(select(Permission.id).where(Permission.id == retired_permission.id))
                    assert not await db.scalar(
                        select(role_menu).where(
                            role_menu.c.role_id == manager_role.id,
                            role_menu.c.menu_id == retired_menu.id,
                        )
                    )
                    final_counts = await _row_counts(db)
                    assert final_counts[:1] == first_counts[:1]
                    assert final_counts[1] == first_counts[1] + 1
                    assert final_counts[2] == first_counts[2]
                    assert final_counts[3] == first_counts[3] + 1
                    assert final_counts[4] == first_counts[4] + 1
                    assert final_counts[5:] == first_counts[5:]
            finally:
                await engine.dispose()

    asyncio.run(scenario())
