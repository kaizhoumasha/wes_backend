"""运行时表并入业务 schema 时保留现有执行事实。"""

from __future__ import annotations

import asyncpg
import pytest

from tests.support.postgresql_heavy import run_alembic, temporary_database

BASE_REVISION = "334c5ca5b81d"
RUNTIME_TABLES = {
    "transport_tasks",
    "transport_members",
    "transport_evidence",
    "transport_callback_receipts",
    "transport_debug_runs",
    "transport_debug_run_steps",
    "transport_debug_position_projections",
    "workline_runtime_status_projections",
}


@pytest.mark.asyncio
async def test_runtime_tables_move_to_biz_without_losing_facts() -> None:
    async with temporary_database() as (_database, database_url):
        run_alembic("upgrade", BASE_REVISION, database_url=database_url)
        connection = await asyncpg.connect(database_url.replace("postgresql+asyncpg", "postgresql", 1))
        try:
            await connection.execute(
                """
                INSERT INTO wes_runtime.transport_tasks (
                    transport_task_id, client_request_id, request_digest, kind, caller_json, request_json,
                    submit_operation_id, submit_timestamp_ms, submit_request_body, submit_request_body_digest,
                    status, submit_attempt_count, outcome_version, published_outcome_version,
                    last_applied_wms_outcome_revision, created_at, updated_at
                ) VALUES (
                    'schema-move-task', 'schema-move-request', repeat('a', 64), 'RACK_MOVE', '{}'::json, '{}'::json,
                    '019f12d0-58d7-7b4d-a23a-1b90aa5d4472', 1, '{}', repeat('b', 64),
                    'RECONCILING', 0, 0, 0, 0, now(), now()
                )
                """
            )
            await connection.execute(
                """
                INSERT INTO wes_runtime.workline_runtime_status_projections
                    (workline_id, runtime_status, source, evidence_json)
                VALUES (1, 'STOPPED', 'schema-move-test', '{}'::json)
                """
            )
        finally:
            await connection.close()

        run_alembic("upgrade", "head", database_url=database_url)
        connection = await asyncpg.connect(database_url.replace("postgresql+asyncpg", "postgresql", 1))
        try:
            schemas = await connection.fetch(
                "SELECT nspname FROM pg_namespace WHERE nspname LIKE 'wes_%' ORDER BY nspname"
            )
            assert [row[0] for row in schemas] == ["wes_biz", "wes_sys"]
            tables = await connection.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'wes_biz' AND tablename = ANY($1::text[])",
                sorted(RUNTIME_TABLES),
            )
            assert {row[0] for row in tables} == RUNTIME_TABLES
            assert (
                await connection.fetchval(
                    "SELECT status FROM wes_biz.transport_tasks WHERE transport_task_id = 'schema-move-task'"
                )
                == "RECONCILING"
            )
            assert (
                await connection.fetchval(
                    "SELECT source FROM wes_biz.workline_runtime_status_projections WHERE workline_id = 1"
                )
                == "schema-move-test"
            )
            assert (
                await connection.fetchval("SELECT to_regclass('wes_biz.ix_transport_tasks_submit_claim')") is not None
            )
            assert (
                await connection.fetchval(
                    """
                SELECT count(*) FROM pg_constraint
                WHERE conrelid = 'wes_biz.transport_members'::regclass
                  AND confrelid = 'wes_biz.transport_tasks'::regclass
                  AND contype = 'f'
                """
                )
                == 1
            )
        finally:
            await connection.close()
