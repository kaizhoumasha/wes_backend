"""菜单持久化删除迁移的 PostgreSQL 验收。"""

from __future__ import annotations

import asyncio

import pytest

from tests.support.postgresql_heavy import connect, run_alembic, temporary_database

PREDECESSOR_REVISION = "d68e6be4006e"
HEAD_REVISION = "9624cc34fa93"


@pytest.mark.integration
def test_menu_tables_are_dropped_without_changing_authorization_tables() -> None:
    async def scenario() -> None:
        async with temporary_database() as (database, database_url):
            run_alembic("upgrade", PREDECESSOR_REVISION, database_url=database_url)

            connection = await connect(database)
            try:
                role_id = await connection.fetchval(
                    """
                    INSERT INTO wes_sys.roles (created_at, name)
                    VALUES (CURRENT_TIMESTAMP, 'menu-cutover-role')
                    RETURNING id
                    """
                )
                permission_id = await connection.fetchval(
                    """
                    INSERT INTO wes_sys.permissions (
                        created_at, tree_path, level, sort_order, has_children, name, type,
                        category, resource, action, method, path
                    ) VALUES (
                        CURRENT_TIMESTAMP, '', 0, 0, FALSE, 'menu:cutover:read', 'user_api',
                        'menu', 'cutover', 'read', 'GET', '/menu-cutover'
                    )
                    RETURNING id
                    """
                )
                menu_id = await connection.fetchval(
                    """
                    INSERT INTO wes_sys.menus (
                        created_at, tree_path, level, sort_order, has_children,
                        name, title, path, is_hidden
                    ) VALUES (
                        CURRENT_TIMESTAMP, '', 0, 0, FALSE,
                        'menu:cutover', 'Menu cutover', '/menu-cutover', FALSE
                    )
                    RETURNING id
                    """
                )
                await connection.execute(
                    "INSERT INTO wes_sys.role_permissions (role_id, permission_id) VALUES ($1, $2)",
                    role_id,
                    permission_id,
                )
                await connection.execute(
                    "INSERT INTO wes_sys.role_menus (role_id, menu_id) VALUES ($1, $2)",
                    role_id,
                    menu_id,
                )
            finally:
                await connection.close()

            run_alembic("upgrade", HEAD_REVISION, database_url=database_url)

            connection = await connect(database)
            try:
                assert await connection.fetchval("SELECT to_regclass('wes_sys.role_menus')") is None
                assert await connection.fetchval("SELECT to_regclass('wes_sys.menus')") is None
                assert await connection.fetchval("SELECT to_regclass('wes_sys.roles')") == "wes_sys.roles"
                assert await connection.fetchval("SELECT to_regclass('wes_sys.permissions')") == "wes_sys.permissions"
                assert await connection.fetchval("SELECT count(*) FROM wes_sys.roles WHERE id = $1", role_id) == 1
                assert (
                    await connection.fetchval(
                        "SELECT count(*) FROM wes_sys.permissions WHERE id = $1",
                        permission_id,
                    )
                    == 1
                )
                assert (
                    await connection.fetchval(
                        """
                        SELECT count(*) FROM wes_sys.role_permissions
                        WHERE role_id = $1 AND permission_id = $2
                        """,
                        role_id,
                        permission_id,
                    )
                    == 1
                )
            finally:
                await connection.close()

    asyncio.run(scenario())
