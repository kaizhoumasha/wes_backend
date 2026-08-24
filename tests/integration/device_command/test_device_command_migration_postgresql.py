"""DeviceCommand 联调合同的 PostgreSQL 迁移切换验证。"""

from __future__ import annotations

import asyncio

import pytest

from tests.support.runtime_inbox_postgresql import connect, run_alembic, temporary_database

PREDECESSOR_REVISION = "1000c501e52a"
HEAD_REVISION = "11013119b97d"
EVENT_DEBUG_PREDECESSOR_REVISION = "f11b613771fa"
EVENT_DEBUG_HEAD_REVISION = "d68e6be4006e"


async def _insert_legacy_cutover_rows(connection) -> dict[str, int]:
    workline_id = await connection.fetchval(
        """
        INSERT INTO wes_biz.work_lines (
            created_at, line_code, line_name, line_type, is_active
        ) VALUES (
            CURRENT_TIMESTAMP, 'DEVICE-COMMAND-CUTOVER', 'DeviceCommand cutover', 'AUTO', FALSE
        )
        RETURNING id
        """
    )
    session_id = await connection.fetchval(
        """
        INSERT INTO wes_biz.workline_sessions (
            created_at, session_code, workline_id, run_mode, status
        ) VALUES (
            CURRENT_TIMESTAMP, 'DEVICE-COMMAND-CUTOVER', $1, 'AUTO', 'NEW'
        )
        RETURNING id
        """,
        workline_id,
    )
    command_id = await connection.fetchval(
        """
        INSERT INTO wes_biz.device_commands (
            created_at, device_code, execution_ref_type, execution_ref_id,
            contract_key, contract_version, task_type, params, deadline_at,
            command_code, payload_digest, status, attempt_count,
            endpoint_base_url, command_timeout_ms
        ) VALUES (
            CURRENT_TIMESTAMP, 'LEGACY-MANUAL-DEBUG', 'MANUAL_DEBUG',
            'legacy-manual-debug', 'rough_sorter.placement_device', '1.0',
            'PICK_AND_PUT', '{}'::json, CURRENT_TIMESTAMP + INTERVAL '1 hour',
            'CMD-LEGACY-MANUAL-DEBUG', repeat('a', 64), 'PENDING', 0,
            'http://legacy-ecs:8080', 30000
        )
        RETURNING id
        """
    )
    hold_id = await connection.fetchval(
        """
        INSERT INTO wes_biz.runtime_holds (
            created_at, hold_type, status, blocking, workline_id, session_id,
            source_kind, source_reason, source_idempotency_key, source_command_id
        ) VALUES (
            CURRENT_TIMESTAMP, 'MANUAL_HOLD', 'OPEN', TRUE, $1, $2,
            'INTERNAL_EVENT', 'DeviceCommand cutover', 'device-command-cutover-hold', $3
        )
        RETURNING id
        """,
        workline_id,
        session_id,
        command_id,
    )
    item_id = await connection.fetchval(
        """
        INSERT INTO wes_biz.ng_return_items (
            created_at, source_workline_id, source_session_id, material_identity_key,
            material_identity_json, physical_handoff_evidence_json, disposition,
            ng_reason_source, ng_reason_code, ng_reason_label,
            created_from_runtime_hold_id, source_command_id, status
        ) VALUES (
            CURRENT_TIMESTAMP, $1, $2, 'device-command-cutover-material',
            '{}'::json, '{}'::json, 'RETURN_TO_NG',
            'DEVICE_ERROR', 'DEVICE_COMMAND_CUTOVER', 'DeviceCommand cutover',
            $3, $4, 'WAITING_REWORK'
        )
        RETURNING id
        """,
        workline_id,
        session_id,
        hold_id,
        command_id,
    )
    timeline_id = await connection.fetchval(
        """
        INSERT INTO wes_biz.workline_timelines (
            created_at, session_id, workline_id, seq_no, occurred_at,
            stage, action_type, actor_type, status, related_command_id
        ) VALUES (
            CURRENT_TIMESTAMP, $1, $2, 1, CURRENT_TIMESTAMP,
            'DISPATCH_PREPARE', 'COMMAND_SENT', 'ORCHESTRATOR', 'SUCCESS', $3
        )
        RETURNING id
        """,
        session_id,
        workline_id,
        command_id,
    )
    return {"runtime_holds": hold_id, "ng_return_items": item_id, "workline_timelines": timeline_id}


@pytest.mark.integration
def test_manual_debug_audit_cutover_clears_incompatible_rows_and_rebuilds_context_constraint() -> None:
    async def scenario() -> None:
        async with temporary_database() as (database, database_url):
            run_alembic("upgrade", PREDECESSOR_REVISION, database_url=database_url)

            connection = await connect(database)
            try:
                dependent_ids = await _insert_legacy_cutover_rows(connection)
            finally:
                await connection.close()

            run_alembic("upgrade", HEAD_REVISION, database_url=database_url)

            connection = await connect(database)
            try:
                assert await connection.fetchval("SELECT count(*) FROM wes_biz.device_commands") == 0
                for table, column in (
                    ("runtime_holds", "source_command_id"),
                    ("ng_return_items", "source_command_id"),
                    ("workline_timelines", "related_command_id"),
                ):
                    dependent_row = await connection.fetchrow(
                        f"SELECT id, {column} FROM wes_biz.{table} WHERE id = $1",
                        dependent_ids[table],
                    )
                    assert dependent_row is not None
                    assert dependent_row[column] is None
                constraints = {
                    row["conname"]: row["definition"]
                    for row in await connection.fetch(
                        """
                        SELECT constraint_row.conname,
                               pg_get_constraintdef(constraint_row.oid) AS definition
                        FROM pg_constraint AS constraint_row
                        WHERE constraint_row.conrelid = 'wes_biz.device_commands'::regclass
                          AND constraint_row.contype = 'c'
                        """
                    )
                }
                assert (
                    "material_execution_id IS NULL"
                    in constraints["ck_device_commands_device_command_execution_context_complete"]
                )
                assert (
                    "execution_reason IS NOT NULL"
                    in constraints["ck_device_commands_device_command_manual_debug_audit_complete"]
                )
            finally:
                await connection.close()

    asyncio.run(scenario())


@pytest.mark.integration
def test_manual_debug_audit_downgrade_restores_predecessor_context_constraint() -> None:
    async def scenario() -> None:
        async with temporary_database() as (database, database_url):
            run_alembic("upgrade", HEAD_REVISION, database_url=database_url)
            run_alembic("downgrade", PREDECESSOR_REVISION, database_url=database_url)

            connection = await connect(database)
            try:
                assert (
                    await connection.fetchval(
                        """
                        SELECT count(*)
                        FROM information_schema.columns
                        WHERE table_schema = 'wes_biz'
                          AND table_name = 'device_commands'
                          AND column_name = 'execution_reason'
                        """
                    )
                    == 0
                )
                definition = await connection.fetchval(
                    """
                    SELECT pg_get_constraintdef(constraint_row.oid)
                    FROM pg_constraint AS constraint_row
                    WHERE constraint_row.conrelid = 'wes_biz.device_commands'::regclass
                      AND constraint_row.conname =
                          'ck_device_commands_device_command_execution_context_complete'
                    """
                )
                assert "material_execution_id IS NULL" not in definition
            finally:
                await connection.close()

    asyncio.run(scenario())


@pytest.mark.integration
def test_event_debug_upgrade_adds_context_audit_and_identity_constraints() -> None:
    async def scenario() -> None:
        async with temporary_database() as (database, database_url):
            run_alembic("upgrade", EVENT_DEBUG_HEAD_REVISION, database_url=database_url)

            connection = await connect(database)
            try:
                constraints = {
                    row["conname"]: row["definition"]
                    for row in await connection.fetch(
                        """
                        SELECT constraint_row.conname,
                               pg_get_constraintdef(constraint_row.oid) AS definition
                        FROM pg_constraint AS constraint_row
                        WHERE constraint_row.conrelid = 'wes_biz.device_commands'::regclass
                          AND constraint_row.contype = 'c'
                        """
                    )
                }
                assert "EVENT_DEBUG" in constraints["ck_device_commands_device_command_execution_context_complete"]
                assert "EVENT_DEBUG" in constraints["ck_device_commands_device_command_manual_debug_audit_complete"]
                assert (
                    await connection.fetchval(
                        """
                    SELECT count(*)
                    FROM pg_indexes
                    WHERE schemaname = 'wes_biz'
                      AND tablename = 'device_commands'
                      AND indexname = 'ux_device_commands_event_debug_identity'
                    """
                    )
                    == 1
                )
                await connection.execute(
                    """
                    INSERT INTO wes_biz.device_commands (
                        created_at, device_code, execution_ref_type, execution_ref_id,
                        contract_key, contract_version, task_type, params, deadline_at,
                        command_code, payload_digest, status, attempt_count,
                        endpoint_base_url, command_timeout_ms, execution_reason
                    ) VALUES (
                        CURRENT_TIMESTAMP, 'STATION-SCAN11', 'EVENT_DEBUG', 'EVENT:debug-migration',
                        'third_party_integration', '1.1', 'MOVE_FORWARD', '{}'::json,
                        CURRENT_TIMESTAMP + INTERVAL '30 seconds', 'CMD-EVENT-DEBUG-MIGRATION',
                        repeat('a', 64), 'PENDING', 0, 'http://10.24.209.26:8080', 30000,
                        'ECS_EVENT_DEBUG:EVENT:debug-migration'
                    )
                    """
                )
            finally:
                await connection.close()

    asyncio.run(scenario())


@pytest.mark.integration
def test_event_debug_downgrade_removes_debug_rows_and_restores_predecessor_schema() -> None:
    async def scenario() -> None:
        async with temporary_database() as (database, database_url):
            run_alembic("upgrade", EVENT_DEBUG_HEAD_REVISION, database_url=database_url)
            connection = await connect(database)
            try:
                await connection.execute(
                    """
                    INSERT INTO wes_biz.device_commands (
                        created_at, device_code, execution_ref_type, execution_ref_id,
                        contract_key, contract_version, task_type, params, deadline_at,
                        command_code, payload_digest, status, attempt_count,
                        endpoint_base_url, command_timeout_ms, execution_reason
                    ) VALUES (
                        CURRENT_TIMESTAMP, 'STATION-SCAN11', 'EVENT_DEBUG', 'EVENT:debug-downgrade',
                        'third_party_integration', '1.1', 'MOVE_FORWARD', '{}'::json,
                        CURRENT_TIMESTAMP + INTERVAL '30 seconds', 'CMD-EVENT-DEBUG-DOWNGRADE',
                        repeat('b', 64), 'PENDING', 0, 'http://10.24.209.26:8080', 30000,
                        'ECS_EVENT_DEBUG:EVENT:debug-downgrade'
                    )
                    """
                )
            finally:
                await connection.close()

            run_alembic("downgrade", EVENT_DEBUG_PREDECESSOR_REVISION, database_url=database_url)
            connection = await connect(database)
            try:
                assert (
                    await connection.fetchval(
                        "SELECT count(*) FROM wes_biz.device_commands WHERE execution_ref_type = 'EVENT_DEBUG'"
                    )
                    == 0
                )
            finally:
                await connection.close()

    asyncio.run(scenario())
