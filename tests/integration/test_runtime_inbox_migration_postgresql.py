"""RuntimeInbox Revision A/B PostgreSQL 真实升降级回环。

该 heavy integration 测试必须显式运行，并且只创建/删除
``wes_tmp_runtime_inbox_`` 前缀的临时数据库。
"""

from __future__ import annotations

import asyncio
import subprocess

import asyncpg
import pytest

from tests.support.runtime_inbox_postgresql import connect, run_alembic, temporary_database

REVISION_A = "b8a28e1bfec8"
REVISION_B = "ec426c628516"
REVISION_A_PARENT = "f0851c5bcfdb"
MILLISECOND_VALUE = 1_783_699_200_123
AUDIT_ONLY_CODE = "PRE_CUTOVER_AUDIT_ONLY"
RUNTIME_INBOX_CHECKS = {
    "ck_runtime_inbox_kind_valid",
    "ck_runtime_inbox_status_valid",
    "ck_runtime_inbox_conditional_envelope",
}
RUNTIME_INBOX_HOT_INDEXES = {
    "ix_wes_runtime_runtime_inbox_status_received",
    "ix_wes_runtime_runtime_inbox_failed_retry_at",
    "ix_wes_runtime_runtime_inbox_processing_lease",
    "ix_wes_runtime_runtime_inbox_bucket_fifo",
}

DEPENDENT_COLUMNS = {
    "workline_diagnostics": "inbox_id",
    "runtime_holds": "source_inbox_id",
    "smt_inbound_handoff_source_items": "source_pick_inbox_id",
}


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


async def _assert_runtime_inbox_numeric_types(
    connection: asyncpg.Connection, *, include_revision_a_columns: bool = True
) -> None:
    """所有迁移阶段都必须保持毫秒时间 BIGINT、重试计数 INTEGER。"""

    columns = await connection.fetch(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'wes_runtime'
          AND table_name = 'runtime_inbox'
          AND column_name = ANY($1::text[])
        """,
        ["received_at", "processed_at", "failed_at", "next_retry_at", "lease_until", "attempt_count", "max_retries"],
    )
    expected_types = {
        "next_retry_at": "bigint",
        "lease_until": "bigint",
        "attempt_count": "integer",
        "max_retries": "integer",
    }
    if include_revision_a_columns:
        expected_types.update(received_at="bigint", processed_at="bigint", failed_at="bigint")
    assert {row["column_name"]: row["data_type"] for row in columns} == expected_types


async def _insert_millisecond_row(connection: asyncpg.Connection) -> int:
    return await connection.fetchval(
        """
        INSERT INTO wes_runtime.runtime_inbox (
            kind, provider_code, event_type, source_event_id,
            payload_json, payload_hash, payload_schema_version, claim_bucket_key,
            received_at, status, attempt_count, max_retries, next_retry_at, lease_until
        ) VALUES (
            'INTERNAL_EVENT', 'pg-roundtrip', 'MILLISECONDS', 'pg-roundtrip-milliseconds',
            '{}'::json, 'sha256:milliseconds', 1, 'source:pg-roundtrip-milliseconds',
            $1, 'FAILED', 1, 5, $1, $1
        )
        RETURNING id
        """,
        MILLISECOND_VALUE,
    )


async def _assert_millisecond_row(connection: asyncpg.Connection, inbox_id: int) -> None:
    values = await connection.fetchrow(
        """
        SELECT next_retry_at, lease_until
        FROM wes_runtime.runtime_inbox
        WHERE id = $1
        """,
        inbox_id,
    )
    assert tuple(values.values()) == (MILLISECOND_VALUE, MILLISECOND_VALUE)


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

    await _assert_runtime_inbox_numeric_types(connection)
    checks = await connection.fetch(
        """
        SELECT constraint_row.conname
        FROM pg_constraint constraint_row
        JOIN pg_class table_row ON table_row.oid = constraint_row.conrelid
        JOIN pg_namespace namespace_row ON namespace_row.oid = table_row.relnamespace
        WHERE constraint_row.contype = 'c'
          AND namespace_row.nspname = 'wes_runtime'
          AND table_row.relname = 'runtime_inbox'
        """
    )
    assert {row["conname"] for row in checks} >= RUNTIME_INBOX_CHECKS
    indexes = await connection.fetch(
        """
        SELECT indexname
        FROM pg_indexes
        WHERE schemaname = 'wes_runtime' AND tablename = 'runtime_inbox'
        """
    )
    assert {row["indexname"] for row in indexes} >= RUNTIME_INBOX_HOT_INDEXES


async def _assert_revision_a_downgrade_schema(connection: asyncpg.Connection) -> None:
    await _assert_runtime_inbox_numeric_types(connection)
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
        async with temporary_database() as (_database, database_url):
            result = run_alembic("current", database_url=database_url)
            assert "f0851c5bcfdb" not in result.stdout

    asyncio.run(scenario())


@pytest.mark.integration
def test_runtime_inbox_fresh_database_upgrades_to_head_with_named_contracts() -> None:
    """fresh database 必须直达 head，并保留 Revision A/B 的命名 DDL 合同。"""

    async def scenario() -> None:
        async with temporary_database() as (database, database_url):
            run_alembic("upgrade", "head", database_url=database_url)
            connection = await connect(database)
            try:
                await _assert_revision_b_schema(connection)
            finally:
                await connection.close()

    asyncio.run(scenario())


@pytest.mark.integration
def test_runtime_inbox_parent_payloadless_row_becomes_audit_only_at_revision_a() -> None:
    """parent payload-less 行必须成为具备毫秒终态证据的 audit-only 记录。"""

    async def scenario() -> None:
        async with temporary_database() as (database, database_url):
            run_alembic("upgrade", REVISION_A_PARENT, database_url=database_url)
            connection = await connect(database)
            try:
                inbox_id = await connection.fetchval(
                    """
                    INSERT INTO wes_runtime.runtime_inbox (
                        provider_code, event_type, source_event_id, payload_hash, status,
                        attempt_count, max_retries, next_retry_at, lease_until
                    ) VALUES (
                        'legacy-provider', 'LEGACY_EVENT', 'legacy-event-1', 'legacy-hash',
                        'PROCESSING', 2, 5, $1, $1
                    )
                    RETURNING id
                    """,
                    MILLISECOND_VALUE,
                )
            finally:
                await connection.close()

            run_alembic("upgrade", REVISION_A, database_url=database_url)
            connection = await connect(database)
            try:
                row = await connection.fetchrow(
                    """
                    SELECT status, last_error_code, last_error_message, received_at, failed_at,
                           processor_token, lease_until, next_retry_at
                    FROM wes_runtime.runtime_inbox
                    WHERE id = $1
                    """,
                    inbox_id,
                )
                assert row["status"] == "DEAD_LETTER"
                assert row["last_error_code"] == AUDIT_ONLY_CODE
                assert "audit only" in row["last_error_message"]
                assert row["received_at"] == row["failed_at"]
                assert row["received_at"] > 1_000_000_000_000
                assert row["processor_token"] is None
                assert (row["lease_until"], row["next_retry_at"]) == (MILLISECOND_VALUE, MILLISECOND_VALUE)
            finally:
                await connection.close()

    asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.parametrize(
    ("legacy_columns", "expected_error"),
    [
        ({"status": "UNKNOWN"}, "invalid status"),
        ({"provider_code": ""}, "without source identity"),
    ],
)
def test_runtime_inbox_revision_a_rejects_unclassifiable_parent_rows(
    legacy_columns: dict[str, str],
    expected_error: str,
) -> None:
    """非法状态或缺失可信来源身份的旧行必须使迁移显式失败。"""

    async def scenario() -> None:
        async with temporary_database() as (database, database_url):
            run_alembic("upgrade", REVISION_A_PARENT, database_url=database_url)
            connection = await connect(database)
            try:
                await connection.execute(
                    """
                    INSERT INTO wes_runtime.runtime_inbox (
                        provider_code, event_type, status, attempt_count, max_retries
                    ) VALUES ($1, 'LEGACY_EVENT', $2, 0, 5)
                    """,
                    legacy_columns.get("provider_code", "legacy-provider"),
                    legacy_columns.get("status", "RECEIVED"),
                )
            finally:
                await connection.close()

            with pytest.raises(subprocess.CalledProcessError) as captured:
                run_alembic("upgrade", REVISION_A, database_url=database_url)
            assert expected_error in captured.value.stderr

    asyncio.run(scenario())


@pytest.mark.integration
def test_runtime_inbox_revision_a_accepts_canonical_and_rejects_invalid_contract_rows() -> None:
    """PostgreSQL 必须接受 canonical/audit-only，并拒绝非法 kind/status/envelope。"""

    async def scenario() -> None:
        async with temporary_database() as (database, database_url):
            run_alembic("upgrade", REVISION_A, database_url=database_url)
            connection = await connect(database)
            try:
                canonical_id = await connection.fetchval(
                    """
                    INSERT INTO wes_runtime.runtime_inbox (
                        kind, provider_code, event_type, source_event_id,
                        payload_json, payload_hash, payload_schema_version,
                        claim_bucket_key, received_at, status, attempt_count, max_retries
                    ) VALUES (
                        'DEVICE_EVENT', 'provider', 'DEVICE_EVENT', 'canonical-1',
                        '{}'::json, 'sha256:canonical', 1,
                        'source:canonical-1', 1000, 'RECEIVED', 0, 5
                    )
                    RETURNING id
                    """
                )
                assert canonical_id is not None

                invalid_rows = (
                    ("INVALID", "RECEIVED", "invalid-kind", "{}", "hash", 1, "bucket", 1, None),
                    ("DEVICE_EVENT", "UNKNOWN", "invalid-status", "{}", "hash", 1, "bucket", 1, None),
                    ("DEVICE_EVENT", "RECEIVED", "invalid-envelope", None, "hash", 1, "bucket", 1, None),
                    ("DEVICE_EVENT", "RECEIVED", "invalid-audit-code", "{}", "hash", 1, "bucket", 1, AUDIT_ONLY_CODE),
                )
                for row in invalid_rows:
                    with pytest.raises(asyncpg.CheckViolationError):
                        await connection.execute(
                            """
                            INSERT INTO wes_runtime.runtime_inbox (
                                kind, provider_code, event_type, source_event_id,
                                payload_json, payload_hash, payload_schema_version,
                                claim_bucket_key, received_at, status, attempt_count, max_retries,
                                last_error_code
                            ) VALUES ($1, 'provider', 'DEVICE_EVENT', $3, $4::json, $5, $6, $7, $8, $2, 0, 5, $9)
                            """,
                            *row,
                        )
            finally:
                await connection.close()

    asyncio.run(scenario())


@pytest.mark.integration
def test_runtime_inbox_revision_a_b_postgresql_roundtrip() -> None:
    """A → B → A → B 在真实 PostgreSQL 上保持完整 DDL 契约。"""

    async def scenario() -> None:
        async with temporary_database() as (database, database_url):
            run_alembic("upgrade", REVISION_A, database_url=database_url)
            connection = await connect(database)
            try:
                assert await connection.fetchval("SELECT version_num FROM wes_sys.alembic_version") == REVISION_A
                await _assert_runtime_inbox_numeric_types(connection)
                millisecond_inbox_id = await _insert_millisecond_row(connection)
            finally:
                await connection.close()

            # Revision A 的父版本已直接使用 BIGINT；A → parent → A 必须保值且不发生窄化溢出。
            run_alembic("downgrade", REVISION_A_PARENT, database_url=database_url)
            connection = await connect(database)
            try:
                await _assert_runtime_inbox_numeric_types(connection, include_revision_a_columns=False)
                await _assert_millisecond_row(connection, millisecond_inbox_id)
            finally:
                await connection.close()

            run_alembic("upgrade", REVISION_A, database_url=database_url)
            connection = await connect(database)
            try:
                await _assert_runtime_inbox_numeric_types(connection)
                await _assert_millisecond_row(connection, millisecond_inbox_id)
                await connection.execute("DELETE FROM wes_runtime.runtime_inbox WHERE id = $1", millisecond_inbox_id)
                await _insert_legacy_references(connection)
            finally:
                await connection.close()

            run_alembic("upgrade", REVISION_B, database_url=database_url)
            connection = await connect(database)
            try:
                assert await connection.fetchval("SELECT version_num FROM wes_sys.alembic_version") == REVISION_B
                await _assert_revision_b_schema(connection)
            finally:
                await connection.close()

            run_alembic("downgrade", REVISION_A, database_url=database_url)
            connection = await connect(database)
            try:
                assert await connection.fetchval("SELECT version_num FROM wes_sys.alembic_version") == REVISION_A
                await _assert_revision_a_downgrade_schema(connection)
            finally:
                await connection.close()

            run_alembic("upgrade", REVISION_B, database_url=database_url)
            connection = await connect(database)
            try:
                assert await connection.fetchval("SELECT version_num FROM wes_sys.alembic_version") == REVISION_B
                await _assert_revision_b_schema(connection)
            finally:
                await connection.close()

    asyncio.run(scenario())
