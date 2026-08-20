"""Tree parent ID PostgreSQL migration round-trip coverage."""

from __future__ import annotations

import asyncio
import subprocess
from typing import TYPE_CHECKING

import pytest

from src.utils.snowflake import generate_snowflake_id
from tests.support.runtime_inbox_postgresql import connect, run_alembic, temporary_database

if TYPE_CHECKING:
    import asyncpg

PREDECESSOR_REVISION = "0a6378b66e1a"
HEAD_REVISION = "db0859fd3259"
INT32_MAX = 2_147_483_647


async def _insert_permission(
    connection: asyncpg.Connection,
    *,
    permission_id: int,
    name: str,
    parent_id: int | None = None,
    level: int = 1,
) -> None:
    await connection.execute(
        """
        INSERT INTO wes_sys.permissions (
            created_at, id, parent_id, tree_path, level, sort_order,
            has_children, name, type
        ) VALUES (
            CURRENT_TIMESTAMP, $1, $2, $3, $4, 0, FALSE, $5, 'user_api'
        )
        """,
        permission_id,
        parent_id,
        f"/{permission_id}/",
        level,
        name,
    )


async def _insert_menu(
    connection: asyncpg.Connection,
    *,
    menu_id: int,
    name: str,
    parent_id: int | None = None,
    level: int = 1,
) -> None:
    await connection.execute(
        """
        INSERT INTO wes_sys.menus (
            created_at, id, parent_id, tree_path, level, sort_order,
            has_children, name, title, path, is_hidden
        ) VALUES (
            CURRENT_TIMESTAMP, $1, $2, $3, $4, 0, FALSE, $5, $5, $6, FALSE
        )
        """,
        menu_id,
        parent_id,
        f"/{menu_id}/",
        level,
        name,
        f"/{name}",
    )


async def _parent_id_types(connection: asyncpg.Connection) -> dict[str, str]:
    rows = await connection.fetch(
        """
        SELECT table_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'wes_sys'
          AND table_name = ANY($1::text[])
          AND column_name = 'parent_id'
        ORDER BY table_name
        """,
        ["menus", "permissions"],
    )
    return {row["table_name"]: row["data_type"] for row in rows}


async def _tree_parent_ids(
    connection: asyncpg.Connection,
    *,
    table: str,
    ids: list[int],
) -> list[int | None]:
    assert table in {"menus", "permissions"}
    rows = await connection.fetch(
        f"SELECT parent_id FROM wes_sys.{table} WHERE id = ANY($1::bigint[]) ORDER BY level, id",
        ids,
    )
    return [row["parent_id"] for row in rows]


@pytest.mark.integration
def test_tree_parent_ids_accept_snowflake_values_and_downgrade_fails_closed() -> None:
    async def scenario() -> None:
        async with temporary_database() as (database, database_url):
            run_alembic("upgrade", PREDECESSOR_REVISION, database_url=database_url)

            permission_parent_id = generate_snowflake_id()
            menu_parent_id = generate_snowflake_id()
            assert permission_parent_id > INT32_MAX
            assert menu_parent_id > INT32_MAX

            connection = await connect(database)
            try:
                await _insert_permission(
                    connection,
                    permission_id=permission_parent_id,
                    name="migration:permission:parent",
                )
                await _insert_menu(connection, menu_id=menu_parent_id, name="migration-menu-parent")
            finally:
                await connection.close()

            run_alembic("upgrade", HEAD_REVISION, database_url=database_url)

            permission_child_id = generate_snowflake_id()
            menu_child_id = generate_snowflake_id()
            fresh_permission_ids = [generate_snowflake_id() for _ in range(3)]
            fresh_menu_ids = [generate_snowflake_id() for _ in range(3)]
            connection = await connect(database)
            try:
                assert await _parent_id_types(connection) == {"menus": "bigint", "permissions": "bigint"}
                await _insert_permission(
                    connection,
                    permission_id=permission_child_id,
                    name="migration:permission:child",
                    parent_id=permission_parent_id,
                    level=2,
                )
                await _insert_menu(
                    connection,
                    menu_id=menu_child_id,
                    name="migration-menu-child",
                    parent_id=menu_parent_id,
                    level=2,
                )

                for index, permission_id in enumerate(fresh_permission_ids):
                    await _insert_permission(
                        connection,
                        permission_id=permission_id,
                        name=f"fresh:permission:{index}",
                        parent_id=fresh_permission_ids[index - 1] if index else None,
                        level=index + 1,
                    )
                for index, menu_id in enumerate(fresh_menu_ids):
                    await _insert_menu(
                        connection,
                        menu_id=menu_id,
                        name=f"fresh-menu-{index}",
                        parent_id=fresh_menu_ids[index - 1] if index else None,
                        level=index + 1,
                    )

                assert await _tree_parent_ids(
                    connection,
                    table="permissions",
                    ids=[permission_parent_id, permission_child_id],
                ) == [None, permission_parent_id]
                assert await _tree_parent_ids(connection, table="menus", ids=[menu_parent_id, menu_child_id]) == [
                    None,
                    menu_parent_id,
                ]
                assert await _tree_parent_ids(
                    connection,
                    table="permissions",
                    ids=fresh_permission_ids,
                ) == [None, fresh_permission_ids[0], fresh_permission_ids[1]]
                assert await _tree_parent_ids(connection, table="menus", ids=fresh_menu_ids) == [
                    None,
                    fresh_menu_ids[0],
                    fresh_menu_ids[1],
                ]
            finally:
                await connection.close()

            with pytest.raises(subprocess.CalledProcessError) as exc_info:
                run_alembic("downgrade", PREDECESSOR_REVISION, database_url=database_url)
            downgrade_output = f"{exc_info.value.stdout}\n{exc_info.value.stderr}"
            assert "wes_sys.permissions" in downgrade_output
            assert "wes_sys.menus" in downgrade_output

            connection = await connect(database)
            try:
                assert await connection.fetchval("SELECT version_num FROM wes_sys.alembic_version") == HEAD_REVISION
                assert await _parent_id_types(connection) == {"menus": "bigint", "permissions": "bigint"}
                assert (
                    await connection.fetchval(
                        "SELECT parent_id FROM wes_sys.permissions WHERE id = $1",
                        permission_child_id,
                    )
                    == permission_parent_id
                )
                assert (
                    await connection.fetchval(
                        "SELECT parent_id FROM wes_sys.menus WHERE id = $1",
                        menu_child_id,
                    )
                    == menu_parent_id
                )
            finally:
                await connection.close()

    asyncio.run(scenario())


@pytest.mark.integration
def test_tree_parent_ids_downgrade_when_all_references_fit_int32() -> None:
    async def scenario() -> None:
        async with temporary_database() as (database, database_url):
            run_alembic("upgrade", PREDECESSOR_REVISION, database_url=database_url)
            run_alembic("upgrade", HEAD_REVISION, database_url=database_url)

            connection = await connect(database)
            try:
                await _insert_permission(connection, permission_id=1_001, name="safe:permission:parent")
                await _insert_permission(
                    connection,
                    permission_id=1_002,
                    name="safe:permission:child",
                    parent_id=1_001,
                    level=2,
                )
                await _insert_menu(connection, menu_id=2_001, name="safe-menu-parent")
                await _insert_menu(connection, menu_id=2_002, name="safe-menu-child", parent_id=2_001, level=2)
            finally:
                await connection.close()

            run_alembic("downgrade", PREDECESSOR_REVISION, database_url=database_url)

            connection = await connect(database)
            try:
                assert (
                    await connection.fetchval("SELECT version_num FROM wes_sys.alembic_version") == PREDECESSOR_REVISION
                )
                assert await _parent_id_types(connection) == {"menus": "integer", "permissions": "integer"}
                assert await connection.fetchval("SELECT parent_id FROM wes_sys.permissions WHERE id = 1002") == 1_001
                assert await connection.fetchval("SELECT parent_id FROM wes_sys.menus WHERE id = 2002") == 2_001
            finally:
                await connection.close()

    asyncio.run(scenario())
