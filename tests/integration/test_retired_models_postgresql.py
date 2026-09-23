"""退役事实表的 PostgreSQL 迁移必须拒绝丢弃已有事实。"""

from __future__ import annotations

import subprocess

import asyncpg
import pytest

from tests.support.postgresql_heavy import run_alembic, temporary_database

PARENT_REVISION = "510f5006d385"
RETIRED_TABLES = ("device_status_observations", "runtime_location_events")


@pytest.mark.asyncio
async def test_retired_tables_require_empty_data_and_downgrade_restores_structure() -> None:
    async with temporary_database() as (_database, database_url):
        run_alembic("upgrade", PARENT_REVISION, database_url=database_url)
        connection = await asyncpg.connect(database_url.replace("postgresql+asyncpg", "postgresql", 1))
        try:
            await connection.execute(
                """
                INSERT INTO wes_biz.runtime_location_events
                    (created_at, object_type, object_key, location_scope, location_code,
                     business_step, source, evidence_json, occurred_at)
                VALUES (now(), 'RACK', 'retirement-test', 'ZONE', 'Z1',
                        'observe', 'test', '{}'::json, now())
                """
            )
            with pytest.raises(subprocess.CalledProcessError) as error:
                run_alembic("upgrade", "head", database_url=database_url)
            assert "contains historical facts" in error.value.stderr
            assert await connection.fetchval("SELECT count(*) FROM wes_biz.runtime_location_events") == 1
            await connection.execute("DELETE FROM wes_biz.runtime_location_events WHERE object_key = 'retirement-test'")
        finally:
            await connection.close()

        run_alembic("upgrade", "head", database_url=database_url)
        connection = await asyncpg.connect(database_url.replace("postgresql+asyncpg", "postgresql", 1))
        try:
            for table in RETIRED_TABLES:
                assert await connection.fetchval("SELECT to_regclass($1)", f"wes_biz.{table}") is None
        finally:
            await connection.close()

        run_alembic("downgrade", PARENT_REVISION, database_url=database_url)
        connection = await asyncpg.connect(database_url.replace("postgresql+asyncpg", "postgresql", 1))
        try:
            for table in RETIRED_TABLES:
                assert await connection.fetchval("SELECT to_regclass($1)", f"wes_biz.{table}") is not None
        finally:
            await connection.close()
