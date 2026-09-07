"""WorkLine 退役迁移的干净基线与历史证据保护。"""

from __future__ import annotations

import subprocess

import asyncpg
import pytest

from tests.support.postgresql_catalog import assert_database_head
from tests.support.postgresql_heavy import run_alembic, temporary_database

BASE_REVISION = "5098dc1b2b63"
RETIREMENT_REVISION = "93deacda8c9c"
pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_retirement_migration_removes_run_entities_and_preserves_command_contract() -> None:
    async with temporary_database() as (_database, database_url):
        run_alembic("upgrade", RETIREMENT_REVISION, database_url=database_url)
        # 空基线可逆；重新升级仍须得到同一最终结构。
        run_alembic("downgrade", BASE_REVISION, database_url=database_url)
        run_alembic("upgrade", RETIREMENT_REVISION, database_url=database_url)
        connection = await asyncpg.connect(database_url.replace("postgresql+asyncpg", "postgresql", 1))
        try:
            await assert_database_head(connection, RETIREMENT_REVISION)
            tables = set(await connection.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'wes_biz'"))
            assert not {
                ("bin_executions",),
                ("line_run_epochs",),
                ("line_run_epoch_device_bindings",),
                ("line_run_epoch_position_bindings",),
            } & {tuple(row) for row in tables}
            columns = await connection.fetch(
                """
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema IN ('wes_biz', 'wes_runtime')
                """
            )
            by_table: dict[str, set[str]] = {}
            for row in columns:
                by_table.setdefault(row["table_name"], set()).add(row["column_name"])
                assert row["column_name"] not in {
                    "line_run_epoch_id",
                    "bin_execution_id",
                    "device_binding_id",
                    "authority_line_run_epoch_id",
                    "authority_bin_execution_id",
                }
            assert {"plugin_version", "flow_mode", "device_contracts", "position_bindings"} <= by_table["work_lines"]
            assert {
                "workline_id",
                "payload_digest",
                "endpoint_base_url",
                "command_timeout_ms",
                "status_max_age_ms",
            } <= by_table["device_commands"]
        finally:
            await connection.close()


@pytest.mark.asyncio
async def test_retirement_migration_rejects_existing_evidence_without_changing_it() -> None:
    async with temporary_database() as (_database, database_url):
        run_alembic("upgrade", BASE_REVISION, database_url=database_url)
        connection = await asyncpg.connect(database_url.replace("postgresql+asyncpg", "postgresql", 1))
        try:
            evidence_id = await connection.fetchval(
                """
                INSERT INTO wes_biz.inbound_evidences
                    (created_at, kind, source_identity, payload_digest, normalized_payload,
                     received_at, device_code, apply_status, decision_attempt_count)
                VALUES ('2026-09-06', 'DEVICE_EVENT', 'retirement-existing-evidence', $1,
                        '{"original":true}', '2026-09-06', 'SCAN-1', 'PENDING', 0)
                RETURNING id
                """,
                "a" * 64,
            )
            before = await connection.fetchrow("SELECT * FROM wes_biz.inbound_evidences WHERE id = $1", evidence_id)
            with pytest.raises(subprocess.CalledProcessError) as rejected:
                run_alembic("upgrade", RETIREMENT_REVISION, database_url=database_url)
            assert "requires an empty execution baseline" in rejected.value.stderr
            await assert_database_head(connection, BASE_REVISION)
            after = await connection.fetchrow("SELECT * FROM wes_biz.inbound_evidences WHERE id = $1", evidence_id)
            assert after == before
            assert await connection.fetchval("SELECT to_regclass('wes_biz.line_run_epochs')") is not None
        finally:
            await connection.close()
