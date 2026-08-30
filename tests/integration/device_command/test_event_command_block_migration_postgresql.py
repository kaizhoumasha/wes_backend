"""EVENT DeviceCommand blocker schema 的 PostgreSQL 合同。"""

from __future__ import annotations

import asyncio

import asyncpg
import pytest

from tests.support.postgresql_heavy import connect, run_alembic, temporary_database

PREDECESSOR_REVISION = "9624cc34fa93"
HEAD_REVISION = "71eeea05c864"
EVIDENCE_ID = 2_147_483_701
COMMAND_ID = 2_147_483_702


async def _insert_dependencies(connection: asyncpg.Connection) -> None:
    await connection.execute(
        """
        INSERT INTO wes_biz.inbound_evidences (
            id, created_at, kind, source_identity, payload_digest,
            normalized_payload, received_at, device_code, contract_key,
            contract_version, apply_status, decision_attempt_count
        ) VALUES (
            $1, CURRENT_TIMESTAMP, 'DEVICE_EVENT', 'EVENT:blocker-migration',
            repeat('a', 64), '{}'::json, CURRENT_TIMESTAMP, 'STATION_SCAN10',
            'third_party_integration', '1.1', 'RECONCILING', 0
        )
        """,
        EVIDENCE_ID,
    )
    await connection.execute(
        """
        INSERT INTO wes_biz.device_commands (
            id, created_at, device_code, execution_ref_type, execution_ref_id,
            contract_key, contract_version, task_type, params, deadline_at,
            command_code, payload_digest, status, attempt_count,
            endpoint_base_url, command_timeout_ms, execution_reason,
            reconciliation_reason
        ) VALUES (
            $1, CURRENT_TIMESTAMP, 'STATION_SCAN10', 'EVENT_DEBUG',
            'EVENT:blocking-command', 'third_party_integration', '1.1',
            'MOVE_FORWARD', '{}'::json, CURRENT_TIMESTAMP + INTERVAL '30 seconds',
            'CMD-OLD-001', repeat('b', 64), 'RECONCILING', 0,
            'http://10.24.209.26:8080', 30000,
            'ECS_EVENT_DEBUG:EVENT:blocking-command', 'DELIVERY_UNKNOWN'
        )
        """,
        COMMAND_ID,
    )


async def _insert_block(
    connection: asyncpg.Connection,
    *,
    block_id: int,
    status: str = "BLOCKED",
    blocking_command_status: str = "RECONCILING",
    reason_code: str = "DEVICE_HAS_ACTIVE_COMMAND",
    requeued: bool = False,
) -> None:
    await connection.execute(
        """
        INSERT INTO wes_biz.device_event_command_blocks (
            id, created_at, evidence_id, source_event_id, device_code,
            blocking_command_id, blocking_command_code,
            blocking_command_status, blocking_reconciliation_reason,
            reason_code, status, blocked_at, requeued_at
        ) VALUES (
            $1, CURRENT_TIMESTAMP, $2, 'EVENT:blocker-migration', 'STATION_SCAN10',
            $3, 'CMD-OLD-001', $4, 'DELIVERY_UNKNOWN', $5, $6,
            CURRENT_TIMESTAMP,
            CASE WHEN $7 THEN CURRENT_TIMESTAMP ELSE NULL END
        )
        """,
        block_id,
        EVIDENCE_ID,
        COMMAND_ID,
        blocking_command_status,
        reason_code,
        status,
        requeued,
    )


@pytest.mark.integration
def test_event_command_block_schema_enforces_bigint_history_and_open_block_invariants() -> None:
    async def scenario() -> None:
        async with temporary_database() as (database, database_url):
            run_alembic("upgrade", PREDECESSOR_REVISION, database_url=database_url)
            connection = await connect(database)
            try:
                assert await connection.fetchval("SELECT to_regclass('wes_biz.device_event_command_blocks')") is None
            finally:
                await connection.close()

            run_alembic("upgrade", HEAD_REVISION, database_url=database_url)
            connection = await connect(database)
            try:
                await _insert_dependencies(connection)
                await _insert_block(connection, block_id=2_147_483_703)

                column_types = {
                    row["column_name"]: row["data_type"]
                    for row in await connection.fetch(
                        """
                        SELECT column_name, data_type
                        FROM information_schema.columns
                        WHERE table_schema = 'wes_biz'
                          AND table_name = 'device_event_command_blocks'
                          AND column_name IN ('id', 'evidence_id', 'blocking_command_id')
                        """
                    )
                }
                assert column_types == {
                    "id": "bigint",
                    "evidence_id": "bigint",
                    "blocking_command_id": "bigint",
                }

                indexes = {
                    row["indexname"]: row["indexdef"]
                    for row in await connection.fetch(
                        """
                        SELECT indexname, indexdef
                        FROM pg_indexes
                        WHERE schemaname = 'wes_biz'
                          AND tablename = 'device_event_command_blocks'
                        """
                    )
                }
                assert "(evidence_id, blocked_at, id)" in indexes["ix_device_event_command_blocks_evidence_history"]
                assert "WHERE" in indexes["ux_device_event_command_blocks_open_evidence"]
                assert "status" in indexes["ux_device_event_command_blocks_open_evidence"]
                assert "'BLOCKED'" in indexes["ux_device_event_command_blocks_open_evidence"]

                with pytest.raises(asyncpg.UniqueViolationError):
                    async with connection.transaction():
                        await _insert_block(connection, block_id=2_147_483_704)

                await connection.execute(
                    """
                    UPDATE wes_biz.device_event_command_blocks
                    SET status = 'REQUEUED', requeued_at = CURRENT_TIMESTAMP
                    WHERE id = $1
                    """,
                    2_147_483_703,
                )
                await _insert_block(connection, block_id=2_147_483_704)
                await connection.execute(
                    """
                    UPDATE wes_biz.device_event_command_blocks
                    SET status = 'REQUEUED', requeued_at = CURRENT_TIMESTAMP
                    WHERE id = $1
                    """,
                    2_147_483_704,
                )

                for kwargs in (
                    {"block_id": 2_147_483_705, "status": "BLOCKED", "requeued": True},
                    {"block_id": 2_147_483_706, "status": "REQUEUED", "requeued": False},
                    {
                        "block_id": 2_147_483_707,
                        "status": "REQUEUED",
                        "requeued": True,
                        "blocking_command_status": "SUCCEEDED",
                    },
                    {
                        "block_id": 2_147_483_708,
                        "status": "REQUEUED",
                        "requeued": True,
                        "reason_code": "UNKNOWN_REASON",
                    },
                ):
                    with pytest.raises(asyncpg.CheckViolationError):
                        async with connection.transaction():
                            await _insert_block(connection, **kwargs)
            finally:
                await connection.close()

            run_alembic("downgrade", PREDECESSOR_REVISION, database_url=database_url)
            connection = await connect(database)
            try:
                assert await connection.fetchval("SELECT to_regclass('wes_biz.device_event_command_blocks')") is None
            finally:
                await connection.close()

    asyncio.run(scenario())
