"""Execution authority 与统一 current projection 的 direct-cutover migration 合同。"""

from __future__ import annotations

import asyncio
import subprocess

import asyncpg
import pytest

from tests.support.postgresql_heavy import connect, run_alembic, temporary_database

PREDECESSOR_REVISION = "7bdca6f754ee"
HEAD_REVISION = "baf328359533"
MATERIAL_FIFO_HEAD_REVISION = "273898a3f09b"
SAFETY_EVIDENCE_HEAD_REVISION = "dd35f04b258f"


@pytest.mark.integration
def test_projection_cutover_fails_closed_with_unclosed_transport_work() -> None:
    async def scenario() -> None:
        async with temporary_database() as (database, database_url):
            run_alembic("upgrade", PREDECESSOR_REVISION, database_url=database_url)
            connection = await connect(database)
            try:
                await connection.execute(
                    """
                    INSERT INTO wes_runtime.transport_tasks (
                        id, transport_task_id, client_request_id, request_digest, kind,
                        caller_json, request_json, submit_operation_id, submit_timestamp_ms,
                        submit_request_body, submit_request_body_digest, status,
                        submit_attempt_count, outcome_version, published_outcome_version,
                        last_applied_wms_outcome_revision, created_at, updated_at
                    ) VALUES (
                        9001, 'transport-migration-blocker', 'request-migration-blocker',
                        repeat('a', 64), 'RACK_MOVE', '{}'::json, '{}'::json,
                        '019d0000-0000-7000-8000-000000000001', 1, '{}', repeat('b', 64),
                        'PENDING', 0, 0, 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                )
            finally:
                await connection.close()

            with pytest.raises(subprocess.CalledProcessError):
                run_alembic("upgrade", HEAD_REVISION, database_url=database_url)

    asyncio.run(scenario())


@pytest.mark.integration
def test_projection_cutover_replaces_old_table_and_installs_authority_constraints() -> None:
    async def scenario() -> None:
        async with temporary_database() as (database, database_url):
            run_alembic("upgrade", PREDECESSOR_REVISION, database_url=database_url)
            connection = await connect(database)
            try:
                assert (
                    await connection.fetchval("SELECT to_regclass('wes_runtime.transport_position_projections')")
                    is not None
                )
                assert await connection.fetchval("SELECT to_regclass('wes_biz.position_projections')") is None
            finally:
                await connection.close()

            run_alembic("upgrade", HEAD_REVISION, database_url=database_url)
            connection = await connect(database)
            try:
                assert (
                    await connection.fetchval("SELECT to_regclass('wes_runtime.transport_position_projections')")
                    is None
                )
                assert await connection.fetchval("SELECT to_regclass('wes_biz.bin_executions')") is not None
                assert await connection.fetchval("SELECT to_regclass('wes_biz.position_projections')") is not None
                authority_columns = {
                    row["column_name"]
                    for row in await connection.fetch(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_schema = 'wes_runtime'
                          AND table_name = 'transport_tasks'
                          AND column_name LIKE 'authority_%'
                        """
                    )
                }
                assert authority_columns == {
                    "authority_workline_id",
                    "authority_line_run_epoch_id",
                    "authority_bin_execution_id",
                }
                active_bin_index = await connection.fetchval(
                    """
                    SELECT indexdef FROM pg_indexes
                    WHERE schemaname = 'wes_biz'
                      AND tablename = 'bin_executions'
                      AND indexname = 'ux_bin_executions_active_bin'
                    """
                )
                assert active_bin_index is not None
                assert "WHERE" in active_bin_index and "ACTIVE" in active_bin_index
            finally:
                await connection.close()

    asyncio.run(scenario())


@pytest.mark.integration
def test_material_fifo_cutover_fails_closed_with_active_material_execution() -> None:
    async def scenario() -> None:
        async with temporary_database() as (database, database_url):
            run_alembic("upgrade", HEAD_REVISION, database_url=database_url)
            connection = await connect(database)
            try:
                await connection.execute("SET session_replication_role = replica")
                await connection.execute(
                    """
                    INSERT INTO wes_biz.material_executions (
                        version, created_at, id, execution_code, material_trace_id,
                        workline_id, line_run_epoch_id, status, last_transition_reason,
                        last_transition_evidence_id, status_changed_at
                    ) VALUES (
                        0, CURRENT_TIMESTAMP, 9001, 'migration-active-material',
                        'migration-active-trace', 9001, 9001, 'HOLD', 'MIGRATION_TEST',
                        9001, CURRENT_TIMESTAMP
                    )
                    """
                )
                await connection.execute("SET session_replication_role = origin")
            finally:
                await connection.close()

            with pytest.raises(subprocess.CalledProcessError):
                run_alembic("upgrade", MATERIAL_FIFO_HEAD_REVISION, database_url=database_url)

    asyncio.run(scenario())


@pytest.mark.integration
def test_material_fifo_cutover_installs_admission_constraint_and_partial_index() -> None:
    async def scenario() -> None:
        async with temporary_database() as (database, database_url):
            run_alembic("upgrade", MATERIAL_FIFO_HEAD_REVISION, database_url=database_url)
            connection = await connect(database)
            try:
                columns = {
                    row["column_name"]
                    for row in await connection.fetch(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_schema = 'wes_biz'
                          AND table_name = 'material_executions'
                          AND column_name LIKE 'admission_%'
                        """
                    )
                }
                assert columns == {"admission_received_at", "admission_evidence_id"}
                constraint = await connection.fetchval(
                    """
                    SELECT pg_get_constraintdef(oid)
                    FROM pg_constraint
                    WHERE conrelid = 'wes_biz.material_executions'::regclass
                      AND contype = 'c'
                      AND pg_get_constraintdef(oid) LIKE '%admission_received_at%'
                    """
                )
                assert constraint is not None
                assert "admission_received_at IS NOT NULL" in constraint
                fifo_index = await connection.fetchval(
                    """
                    SELECT indexdef FROM pg_indexes
                    WHERE schemaname = 'wes_biz'
                      AND tablename = 'material_executions'
                      AND indexname = 'ix_material_executions_active_fifo'
                    """
                )
                assert fifo_index is not None
                assert "admission_received_at" in fifo_index
                assert "WHERE" in fifo_index and "CLOSED" in fifo_index
            finally:
                await connection.close()

    asyncio.run(scenario())


@pytest.mark.integration
def test_safety_cutover_installs_final_device_evidence_authority() -> None:
    async def scenario() -> None:
        async with temporary_database() as (database, database_url):
            run_alembic("upgrade", SAFETY_EVIDENCE_HEAD_REVISION, database_url=database_url)
            connection = await connect(database)
            try:
                column_type = await connection.fetchval(
                    """
                    SELECT data_type
                    FROM information_schema.columns
                    WHERE table_schema = 'wes_biz'
                      AND table_name = 'workline_safety_incidents'
                      AND column_name = 'source_evidence_id'
                    """
                )
                target = await connection.fetchval(
                    """
                    SELECT ccu.table_schema || '.' || ccu.table_name || '.' || ccu.column_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.constraint_column_usage ccu
                      ON ccu.constraint_name = tc.constraint_name
                     AND ccu.constraint_schema = tc.constraint_schema
                    WHERE tc.table_schema = 'wes_biz'
                      AND tc.table_name = 'workline_safety_incidents'
                      AND tc.constraint_type = 'FOREIGN KEY'
                      AND tc.constraint_name LIKE '%source_evidence_id%'
                    """
                )
                assert column_type == "bigint"
                assert target == "wes_biz.inbound_evidences.id"
            finally:
                await connection.close()

    asyncio.run(scenario())
