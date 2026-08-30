"""本机开发基础授权在隔离 PostgreSQL 临时库上的收敛验收。"""

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
from src.app.admin.models import Permission, Role, User, role_permission, user_role
from src.core.security import get_password_hash
from tests.support.postgresql_heavy import run_alembic, temporary_database

SEED_PASSWORD = "admin123"


async def _row_counts(db: AsyncSession) -> tuple[int, ...]:
    tables = (Role, User, Permission, user_role, role_permission)
    counts: list[int] = []
    for table in tables:
        counts.append(int(await db.scalar(select(func.count()).select_from(table)) or 0))
    return tuple(counts)


@pytest.mark.integration
def test_dev_seed_is_idempotent_repairs_authorization_drift_and_check_is_read_only() -> None:
    async def scenario() -> None:
        async with temporary_database() as (_database, database_url):
            run_alembic("upgrade", "head", database_url=database_url)
            engine = create_async_engine(database_url, pool_pre_ping=True)
            session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            try:
                async with session_factory() as db:
                    await _seed_foundation_data(db, SEED_PASSWORD)
                    first_counts = await _row_counts(db)
                    assert all(count > 0 for count in first_counts)

                    await _seed_foundation_data(db, SEED_PASSWORD)
                    assert await _row_counts(db) == first_counts

                    with (
                        patch(
                            "scripts.data.seed_initial_data.managed_permission_names_for_app",
                            return_value=set(),
                        ),
                        pytest.raises(RuntimeError, match="权限定义为空"),
                    ):
                        await _seed_foundation_data(db, SEED_PASSWORD)
                    assert await _row_counts(db) == first_counts

                    active_permission = (
                        await db.execute(
                            select(Permission)
                            .where(Permission.is_deleted.is_(False), Permission.action != "group")
                            .limit(1)
                        )
                    ).scalar_one()
                    retired_permission_id = active_permission.id
                    active_permission.is_deleted = True
                    await db.commit()

                    await _seed_foundation_data(db, SEED_PASSWORD)
                    rebuilt_permission = (
                        await db.execute(
                            select(Permission).where(
                                Permission.name == active_permission.name,
                                Permission.is_deleted.is_(False),
                            )
                        )
                    ).scalar_one()
                    assert rebuilt_permission.id != retired_permission_id
                    await _check_foundation_data(db, SEED_PASSWORD)
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
                        insert(role_permission).values(
                            role_id=manager_role.id,
                            permission_id=extra_permission.id,
                        )
                    )

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
                    custom_user = User(
                        username="custom-dev-user",
                        email="custom-dev-user@localhost.localdomain",
                        full_name="Custom Dev User",
                        hashed_password=get_password_hash(SEED_PASSWORD),
                    )
                    db.add_all([retired_permission, custom_user])
                    await db.flush()
                    assert retired_permission.id is not None
                    assert custom_user.id is not None
                    await db.execute(
                        insert(role_permission).values(
                            role_id=manager_role.id,
                            permission_id=retired_permission.id,
                        )
                    )
                    await db.execute(insert(user_role).values(user_id=custom_user.id, role_id=manager_role.id))
                    await db.commit()

                    drift_counts = await _row_counts(db)
                    with pytest.raises(RuntimeError, match="未收敛"):
                        await _check_foundation_data(db, SEED_PASSWORD)
                    assert await _row_counts(db) == drift_counts

                    await _seed_foundation_data(db, SEED_PASSWORD)
                    await _check_foundation_data(db, SEED_PASSWORD)

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
                    final_counts = await _row_counts(db)
                    assert final_counts[0] == first_counts[0]
                    assert final_counts[1] == first_counts[1] + 1
                    assert final_counts[2] == first_counts[2]
                    assert final_counts[3] == first_counts[3] + 1
                    assert final_counts[4] == first_counts[4]
            finally:
                await engine.dispose()

    asyncio.run(scenario())
