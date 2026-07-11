"""RuntimeInbox Revision A/B PostgreSQL 真实升降级回环。

该 heavy integration 测试必须显式运行，并且只创建/删除
``wes_tmp_runtime_inbox_`` 前缀的临时数据库。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.engine import make_url

from src.core.conf import settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

REPO_ROOT = Path(__file__).resolve().parents[2]
SAFE_DATABASE_PREFIX = "wes_tmp_runtime_inbox_"
REVISION_A = "b8a28e1bfec8"
REVISION_B = "ec426c628516"

DEPENDENT_COLUMNS = {
    "workline_diagnostics": "inbox_id",
    "runtime_holds": "source_inbox_id",
    "smt_inbound_handoff_source_items": "source_pick_inbox_id",
}


def _database_url(database: str, *, sqlalchemy_driver: bool) -> str:
    # Heavy integration 可显式指向隔离 PostgreSQL；未设置时保留本地默认行为。
    url = make_url(os.getenv("INTEGRATION_DATABASE_URL") or settings.DATABASE_URL)
    drivername = "postgresql+asyncpg" if sqlalchemy_driver else "postgresql"
    return url.set(drivername=drivername, database=database).render_as_string(hide_password=False)


async def _drop_database(admin: asyncpg.Connection, database: str) -> None:
    assert database.startswith(SAFE_DATABASE_PREFIX)
    quoted_database = '"' + database.replace('"', '""') + '"'
    await admin.execute(f"DROP DATABASE IF EXISTS {quoted_database} WITH (FORCE)")


@asynccontextmanager
async def _temporary_database() -> AsyncIterator[tuple[str, str]]:
    database = f"{SAFE_DATABASE_PREFIX}{uuid4().hex}"
    admin = await asyncpg.connect(_database_url("postgres", sqlalchemy_driver=False))
    try:
        quoted_database = '"' + database.replace('"', '""') + '"'
        await admin.execute(f"CREATE DATABASE {quoted_database}")
        yield database, _database_url(database, sqlalchemy_driver=True)
    finally:
        await _drop_database(admin, database)
        await admin.close()


def _run_alembic(*args: str, database_url: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["ALEMBIC_DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


async def _connect(database: str) -> asyncpg.Connection:
    return await asyncpg.connect(_database_url(database, sqlalchemy_driver=False))


async def _insert_legacy_references(connection: asyncpg.Connection) -> int:
    legacy_inbox_id = await connection.fetchval(
        """
        INSERT INTO wes_biz.workline_inbox (
            created_at, updated_at, idempotency_key, received_at, kind,
            source_system, status, attempt_count, max_attempts, claim_bucket_key
        ) VALUES (
            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'pg-roundtrip-legacy', CURRENT_TIMESTAMP,
            'INTERNAL_EVENT', 'SYSTEM', 'NEW', 0, 5, 'pg-roundtrip'
        )
        RETURNING id
        """
    )

    # 三张依赖表的其他 FK 不属于本迁移验收范围。测试会话内禁用 FK trigger，
    # 但保留 NOT NULL/CHECK 约束，以真实行验证三个 legacy Inbox 引用被清空。
    await connection.execute("SET session_replication_role = replica")
    try:
        await connection.execute(
            """
            INSERT INTO wes_biz.workline_diagnostics (
                diagnostic_key, inbox_id, diagnostic_code, error_domain, severity,
                recoverability, problem_class, owner, status, message
            ) VALUES (
                'pg-roundtrip-diagnostic', $1, 'TEST', 'RUNTIME', 'INFO',
                'AUTO', 'SOFTWARE', 'TEST', 'ACTIVE', 'migration roundtrip'
            )
            """,
            legacy_inbox_id,
        )
        await connection.execute(
            """
            INSERT INTO wes_biz.runtime_holds (
                created_at, hold_type, status, blocking, workline_id, source_kind,
                source_reason, source_idempotency_key, source_inbox_id
            ) VALUES (
                CURRENT_TIMESTAMP, 'MANUAL_HOLD', 'OPEN', TRUE, 987654321,
                'INTERNAL_EVENT', 'migration roundtrip', 'pg-roundtrip-hold', $1
            )
            """,
            legacy_inbox_id,
        )
        await connection.execute(
            """
            INSERT INTO wes_biz.smt_inbound_handoff_source_items (
                created_at, handoff_demand_id, item_key, status, claim_attempt_no,
                source_pick_inbox_id
            ) VALUES (
                CURRENT_TIMESTAMP, 987654321, 'pg-roundtrip-item', 'READY', 0, $1
            )
            """,
            legacy_inbox_id,
        )
    finally:
        await connection.execute("SET session_replication_role = origin")
    return legacy_inbox_id


async def _foreign_key_targets(connection: asyncpg.Connection) -> dict[tuple[str, str], tuple[str, str]]:
    rows = await connection.fetch(
        """
        SELECT source_table.relname AS table_name,
               source_column.attname AS column_name,
               target_namespace.nspname AS target_schema,
               target_table.relname AS target_table
        FROM pg_constraint constraint_row
        JOIN pg_class source_table ON source_table.oid = constraint_row.conrelid
        JOIN pg_namespace source_namespace ON source_namespace.oid = source_table.relnamespace
        JOIN pg_class target_table ON target_table.oid = constraint_row.confrelid
        JOIN pg_namespace target_namespace ON target_namespace.oid = target_table.relnamespace
        JOIN pg_attribute source_column
          ON source_column.attrelid = source_table.oid
         AND source_column.attnum = constraint_row.conkey[1]
        WHERE constraint_row.contype = 'f'
          AND source_namespace.nspname = 'wes_biz'
          AND source_table.relname = ANY($1::text[])
        """,
        list(DEPENDENT_COLUMNS),
    )
    return {
        (row["table_name"], row["column_name"]): (row["target_schema"], row["target_table"])
        for row in rows
        if row["column_name"] == DEPENDENT_COLUMNS[row["table_name"]]
    }


async def _assert_revision_b_schema(connection: asyncpg.Connection) -> None:
    for table_name, column_name in DEPENDENT_COLUMNS.items():
        value = await connection.fetchval(f'SELECT {column_name} FROM wes_biz."{table_name}"')
        assert value is None

    assert await connection.fetchval("SELECT to_regclass('wes_biz.workline_inbox')") is None
    assert await _foreign_key_targets(connection) == dict.fromkeys(
        DEPENDENT_COLUMNS.items(), ("wes_runtime", "runtime_inbox")
    )

    column = await connection.fetchrow(
        """
        SELECT is_nullable, data_type
        FROM information_schema.columns
        WHERE table_schema = 'wes_runtime'
          AND table_name = 'runtime_inbox'
          AND column_name = 'workline_session_id'
        """
    )
    assert dict(column) == {"is_nullable": "YES", "data_type": "bigint"}
    assert (
        await connection.fetchval("SELECT to_regclass('wes_runtime.ix_wes_runtime_runtime_inbox_workline_session_id')")
        is not None
    )
    session_fk_target = await connection.fetchrow(
        """
        SELECT target_namespace.nspname AS target_schema, target_table.relname AS target_table
        FROM pg_constraint constraint_row
        JOIN pg_class source_table ON source_table.oid = constraint_row.conrelid
        JOIN pg_namespace source_namespace ON source_namespace.oid = source_table.relnamespace
        JOIN pg_class target_table ON target_table.oid = constraint_row.confrelid
        JOIN pg_namespace target_namespace ON target_namespace.oid = target_table.relnamespace
        WHERE constraint_row.contype = 'f'
          AND source_namespace.nspname = 'wes_runtime'
          AND source_table.relname = 'runtime_inbox'
          AND constraint_row.conname = 'fk_runtime_inbox_workline_session_id_workline_sessions'
        """
    )
    assert tuple(session_fk_target.values()) == ("wes_biz", "workline_sessions")

    timestamp_columns = await connection.fetch(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'wes_runtime'
          AND table_name = 'runtime_inbox'
          AND column_name = ANY($1::text[])
        """,
        ["received_at", "processed_at", "failed_at", "next_retry_at", "lease_until"],
    )
    assert {row["column_name"]: row["data_type"] for row in timestamp_columns} == {
        "received_at": "bigint",
        "processed_at": "bigint",
        "failed_at": "bigint",
        "next_retry_at": "bigint",
        "lease_until": "bigint",
    }
    retry_columns = await connection.fetch(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'wes_runtime'
          AND table_name = 'runtime_inbox'
          AND column_name = ANY($1::text[])
        """,
        ["attempt_count", "max_retries"],
    )
    assert {row["column_name"]: row["data_type"] for row in retry_columns} == {
        "attempt_count": "integer",
        "max_retries": "integer",
    }


async def _assert_revision_a_downgrade_schema(connection: asyncpg.Connection) -> None:
    assert await connection.fetchval("SELECT to_regclass('wes_biz.workline_inbox')") is not None
    column_count = await connection.fetchval(
        """
        SELECT count(*)
        FROM information_schema.columns
        WHERE table_schema = 'wes_biz' AND table_name = 'workline_inbox'
        """
    )
    constraint_count = await connection.fetchval(
        """
        SELECT count(*)
        FROM pg_constraint constraint_row
        JOIN pg_class table_row ON table_row.oid = constraint_row.conrelid
        JOIN pg_namespace namespace_row ON namespace_row.oid = table_row.relnamespace
        WHERE namespace_row.nspname = 'wes_biz' AND table_row.relname = 'workline_inbox'
        """
    )
    index_count = await connection.fetchval(
        """
        SELECT count(*) FROM pg_indexes
        WHERE schemaname = 'wes_biz' AND tablename = 'workline_inbox'
        """
    )
    assert (column_count, constraint_count, index_count) == (24, 8, 19)
    assert await _foreign_key_targets(connection) == dict.fromkeys(
        DEPENDENT_COLUMNS.items(), ("wes_biz", "workline_inbox")
    )
    assert (
        await connection.fetchval(
            """
        SELECT count(*)
        FROM information_schema.columns
        WHERE table_schema = 'wes_runtime'
          AND table_name = 'runtime_inbox'
          AND column_name = 'workline_session_id'
        """
        )
        == 0
    )


@pytest.mark.integration
def test_alembic_database_url_targets_isolated_database() -> None:
    """Alembic 显式 override 不得回退连接共享 ``wes_db``。"""

    async def scenario() -> None:
        async with _temporary_database() as (_database, database_url):
            result = _run_alembic("current", database_url=database_url)
            assert "f0851c5bcfdb" not in result.stdout

    asyncio.run(scenario())


@pytest.mark.integration
def test_runtime_inbox_revision_a_b_postgresql_roundtrip() -> None:
    """A → B → A → B 在真实 PostgreSQL 上保持完整 DDL 契约。"""

    async def scenario() -> None:
        async with _temporary_database() as (database, database_url):
            _run_alembic("upgrade", REVISION_A, database_url=database_url)
            connection = await _connect(database)
            try:
                assert await connection.fetchval("SELECT version_num FROM wes_sys.alembic_version") == REVISION_A
                await _insert_legacy_references(connection)
            finally:
                await connection.close()

            _run_alembic("upgrade", REVISION_B, database_url=database_url)
            connection = await _connect(database)
            try:
                assert await connection.fetchval("SELECT version_num FROM wes_sys.alembic_version") == REVISION_B
                await _assert_revision_b_schema(connection)
            finally:
                await connection.close()

            _run_alembic("downgrade", REVISION_A, database_url=database_url)
            connection = await _connect(database)
            try:
                assert await connection.fetchval("SELECT version_num FROM wes_sys.alembic_version") == REVISION_A
                await _assert_revision_a_downgrade_schema(connection)
            finally:
                await connection.close()

            _run_alembic("upgrade", REVISION_B, database_url=database_url)
            connection = await _connect(database)
            try:
                assert await connection.fetchval("SELECT version_num FROM wes_sys.alembic_version") == REVISION_B
                assert await connection.fetchval("SELECT to_regclass('wes_biz.workline_inbox')") is None
            finally:
                await connection.close()

    asyncio.run(scenario())
