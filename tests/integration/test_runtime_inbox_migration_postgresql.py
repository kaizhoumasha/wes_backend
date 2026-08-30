"""RuntimeInbox Revision A/B PostgreSQL 真实升降级回环。

该 heavy integration 测试必须显式运行，并且只创建/删除
``wes_tmp_heavy_`` 前缀的临时数据库。
"""

from __future__ import annotations

import asyncio
import subprocess

import asyncpg
import pytest

from tests.support.postgresql_heavy import connect, run_alembic, temporary_database

REVISION_A = "b8a28e1bfec8"
REVISION_B = "ec426c628516"
REVISION_C = "e0d58415afc9"
REVISION_BEFORE_TYPE_REPAIR = "11013119b97d"
REVISION_TYPE_REPAIR = "fe7280088174"
REVISION_A_PARENT = "f0851c5bcfdb"
MILLISECOND_VALUE = 1_783_699_200_123
INTEGER_REPRESENTABLE_VALUE = 1_234_567_890
AUDIT_ONLY_CODE = "PRE_CUTOVER_AUDIT_ONLY"
RUNTIME_INBOX_CHECKS = {
    "ck_runtime_inbox_kind_valid",
    "ck_runtime_inbox_status_valid",
    "ck_runtime_inbox_max_retries_positive",
    "ck_runtime_inbox_conditional_envelope",
}
RUNTIME_INBOX_HOT_INDEXES = {
    "ix_wes_runtime_runtime_inbox_kind",
    "ix_wes_runtime_runtime_inbox_workline_id",
    "ix_wes_runtime_runtime_inbox_device_id",
    "ix_wes_runtime_runtime_inbox_command_id",
    "ix_wes_runtime_runtime_inbox_trace_id",
    "ix_wes_runtime_runtime_inbox_claim_bucket_key",
    "ix_wes_runtime_runtime_inbox_status_received",
    "ix_wes_runtime_runtime_inbox_failed_retry_at",
    "ix_wes_runtime_runtime_inbox_processing_lease",
    "ix_wes_runtime_runtime_inbox_bucket_fifo",
}
RUNTIME_INBOX_REVISION_C_INDEXES = RUNTIME_INBOX_HOT_INDEXES | {
    "ix_wes_runtime_runtime_inbox_workline_session_id",
}

DEPENDENT_COLUMNS = {
    "workline_diagnostics": "inbox_id",
    "runtime_holds": "source_inbox_id",
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
    workline_id = await connection.fetchval(
        """
        INSERT INTO wes_biz.work_lines (
            created_at, line_code, line_name, line_type, is_active
        ) VALUES (
            CURRENT_TIMESTAMP, 'PG-ROUNDTRIP', 'PostgreSQL migration roundtrip', 'AUTO', FALSE
        )
        RETURNING id
        """
    )

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
            CURRENT_TIMESTAMP, 'MANUAL_HOLD', 'OPEN', TRUE, $2,
            'INTERNAL_EVENT', 'migration roundtrip', 'pg-roundtrip-hold', $1
        )
        """,
        legacy_inbox_id,
        workline_id,
    )
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


async def _insert_millisecond_row(connection: asyncpg.Connection, *, value: int = MILLISECOND_VALUE) -> int:
    return await connection.fetchval(
        """
        INSERT INTO wes_runtime.runtime_inbox (
            provider_code, event_type, received_at, failed_at, status,
            attempt_count, max_retries, next_retry_at, lease_until,
            last_error_code, last_error_message
        ) VALUES (
            'pg-roundtrip', 'MILLISECONDS', $1, $2, 'DEAD_LETTER',
            1, 5, $3, $4, 'PRE_CUTOVER_AUDIT_ONLY',
            'Pre-cutover row retained for audit-only millisecond roundtrip'
        )
        RETURNING id
        """,
        value,
        value,
        value,
        value,
    )


async def _assert_millisecond_row(
    connection: asyncpg.Connection, inbox_id: int, *, value: int = MILLISECOND_VALUE
) -> None:
    values = await connection.fetchrow(
        """
        SELECT next_retry_at, lease_until
        FROM wes_runtime.runtime_inbox
        WHERE id = $1
        """,
        inbox_id,
    )
    assert tuple(values.values()) == (value, value)


async def _assert_revision_b_schema(
    connection: asyncpg.Connection,
    *,
    expect_indexes: bool = True,
    legacy_inbox_id: int | None = None,
) -> None:
    mapped_ids: set[int] = set()
    for table_name, column_name in DEPENDENT_COLUMNS.items():
        value = await connection.fetchval(f'SELECT {column_name} FROM wes_biz."{table_name}"')
        if legacy_inbox_id is None:
            assert value is None
        else:
            assert isinstance(value, int)
            mapped_ids.add(value)

    if legacy_inbox_id is not None:
        assert len(mapped_ids) == 1
        audit_row = await connection.fetchrow(
            """
            SELECT provider_code, event_type, source_event_id, status, last_error_code,
                   kind, payload_json
            FROM wes_runtime.runtime_inbox
            WHERE id = $1
            """,
            mapped_ids.pop(),
        )
        assert dict(audit_row) == {
            "provider_code": "LEGACY_WORKLINE_INBOX",
            "event_type": "PRE_CUTOVER_AUDIT_ONLY",
            "source_event_id": f"legacy-workline-inbox:{legacy_inbox_id}",
            "status": "DEAD_LETTER",
            "last_error_code": AUDIT_ONLY_CODE,
            "kind": None,
            "payload_json": None,
        }

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
    workline_session_index = await connection.fetchval(
        "SELECT to_regclass('wes_runtime.ix_wes_runtime_runtime_inbox_workline_session_id')"
    )
    assert (workline_session_index is not None) is expect_indexes
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
        SELECT index_row.relname AS indexname,
               index_metadata.indisvalid,
               index_metadata.indisready
        FROM pg_index AS index_metadata
        JOIN pg_class AS index_row ON index_row.oid = index_metadata.indexrelid
        JOIN pg_class AS table_row ON table_row.oid = index_metadata.indrelid
        JOIN pg_namespace AS namespace_row ON namespace_row.oid = table_row.relnamespace
        WHERE namespace_row.nspname = 'wes_runtime'
          AND table_row.relname = 'runtime_inbox'
        """
    )
    index_states = {row["indexname"]: (bool(row["indisvalid"]), bool(row["indisready"])) for row in indexes}
    if expect_indexes:
        assert index_states.keys() >= RUNTIME_INBOX_REVISION_C_INDEXES
        assert all(index_states[name] == (True, True) for name in RUNTIME_INBOX_REVISION_C_INDEXES)
    else:
        assert not (index_states.keys() & RUNTIME_INBOX_REVISION_C_INDEXES)


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
def test_runtime_inbox_head_repairs_deployed_integer_millisecond_columns() -> None:
    """已 stamp 到旧 head 的联调库若遗留 INTEGER，升级必须恢复为 BIGINT。"""

    async def scenario() -> None:
        async with temporary_database() as (database, database_url):
            run_alembic("upgrade", REVISION_BEFORE_TYPE_REPAIR, database_url=database_url)
            connection = await connect(database)
            try:
                await connection.execute(
                    """
                    ALTER TABLE wes_runtime.runtime_inbox
                        ALTER COLUMN next_retry_at TYPE INTEGER USING next_retry_at::INTEGER,
                        ALTER COLUMN lease_until TYPE INTEGER USING lease_until::INTEGER
                    """
                )
                inbox_id = await _insert_millisecond_row(connection, value=INTEGER_REPRESENTABLE_VALUE)
            finally:
                await connection.close()

            run_alembic("upgrade", REVISION_TYPE_REPAIR, database_url=database_url)
            connection = await connect(database)
            try:
                await _assert_runtime_inbox_numeric_types(connection)
                await _assert_millisecond_row(connection, inbox_id, value=INTEGER_REPRESENTABLE_VALUE)
            finally:
                await connection.close()

            run_alembic("downgrade", REVISION_BEFORE_TYPE_REPAIR, database_url=database_url)
            connection = await connect(database)
            try:
                assert (
                    await connection.fetchval("SELECT version_num FROM wes_sys.alembic_version")
                    == REVISION_BEFORE_TYPE_REPAIR
                )
                await _assert_runtime_inbox_numeric_types(connection)
                await _assert_millisecond_row(connection, inbox_id, value=INTEGER_REPRESENTABLE_VALUE)
            finally:
                await connection.close()

    asyncio.run(scenario())


@pytest.mark.integration
def test_runtime_inbox_revision_c_rebuilds_invalid_concurrent_index() -> None:
    """Revision C 必须替换并发创建失败遗留的同名 invalid index。"""

    async def scenario() -> None:
        async with temporary_database() as (database, database_url):
            run_alembic("upgrade", REVISION_B, database_url=database_url)
            connection = await connect(database)
            try:
                await connection.execute(
                    """
                    INSERT INTO wes_runtime.runtime_inbox (
                        kind, provider_code, event_type, source_event_id,
                        payload_json, payload_hash, payload_schema_version,
                        claim_bucket_key, received_at, status, attempt_count, max_retries
                    ) VALUES
                        ('DEVICE_EVENT', 'TEST', 'DEVICE_EVENT', 'invalid-index-1',
                         '{}'::json, 'hash-invalid-index-1', 1,
                         'source:invalid-index-1', 1, 'RECEIVED', 0, 5),
                        ('DEVICE_EVENT', 'TEST', 'DEVICE_EVENT', 'invalid-index-2',
                         '{}'::json, 'hash-invalid-index-2', 1,
                         'source:invalid-index-2', 2, 'RECEIVED', 0, 5)
                    """
                )
                with pytest.raises(asyncpg.UniqueViolationError):
                    await connection.execute(
                        """
                        CREATE UNIQUE INDEX CONCURRENTLY ix_wes_runtime_runtime_inbox_kind
                        ON wes_runtime.runtime_inbox (kind)
                        """
                    )
                assert not await connection.fetchval(
                    """
                    SELECT index_metadata.indisvalid
                    FROM pg_index AS index_metadata
                    JOIN pg_class AS index_row ON index_row.oid = index_metadata.indexrelid
                    JOIN pg_namespace AS namespace_row ON namespace_row.oid = index_row.relnamespace
                    WHERE namespace_row.nspname = 'wes_runtime'
                      AND index_row.relname = 'ix_wes_runtime_runtime_inbox_kind'
                    """
                )
            finally:
                await connection.close()

            run_alembic("upgrade", REVISION_C, database_url=database_url)
            connection = await connect(database)
            try:
                await _assert_revision_b_schema(connection)
                index_state = await connection.fetchrow(
                    """
                    SELECT index_metadata.indisvalid,
                           index_metadata.indisready,
                           index_metadata.indisunique
                    FROM pg_index AS index_metadata
                    JOIN pg_class AS index_row ON index_row.oid = index_metadata.indexrelid
                    JOIN pg_namespace AS namespace_row ON namespace_row.oid = index_row.relnamespace
                    WHERE namespace_row.nspname = 'wes_runtime'
                      AND index_row.relname = 'ix_wes_runtime_runtime_inbox_kind'
                    """
                )
                assert tuple(index_state.values()) == (True, True, False)
            finally:
                await connection.close()

    asyncio.run(scenario())


@pytest.mark.integration
def test_runtime_inbox_revision_c_downgrade_removes_all_managed_indexes() -> None:
    """Revision C → B 必须并发删除 C 管理的全部索引，不影响 Revision B schema。"""

    async def scenario() -> None:
        async with temporary_database() as (database, database_url):
            run_alembic("upgrade", REVISION_C, database_url=database_url)
            connection = await connect(database)
            try:
                await _assert_revision_b_schema(connection)
            finally:
                await connection.close()

            run_alembic("downgrade", REVISION_B, database_url=database_url)
            connection = await connect(database)
            try:
                assert await connection.fetchval("SELECT version_num FROM wes_sys.alembic_version") == REVISION_B
                await _assert_revision_b_schema(connection, expect_indexes=False)
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
                    (
                        "DEVICE_EVENT",
                        "DEAD_LETTER",
                        "audit-label-with-canonical-payload",
                        "{}",
                        "hash",
                        1,
                        "bucket",
                        1,
                        AUDIT_ONLY_CODE,
                    ),
                )
                for row in invalid_rows:
                    with pytest.raises(asyncpg.CheckViolationError):
                        await connection.execute(
                            """
                            INSERT INTO wes_runtime.runtime_inbox (
                                kind, provider_code, event_type, source_event_id,
                                payload_json, payload_hash, payload_schema_version,
                                claim_bucket_key, received_at, status, attempt_count, max_retries,
                                last_error_code, last_error_message, failed_at
                            ) VALUES (
                                $1, 'provider', 'DEVICE_EVENT', $3, $4::json, $5, $6, $7, $8::bigint, $2, 0, 5,
                                $9::varchar,
                                CASE WHEN $9::varchar = 'PRE_CUTOVER_AUDIT_ONLY' THEN 'audit evidence' ELSE NULL END,
                                CASE WHEN $9::varchar = 'PRE_CUTOVER_AUDIT_ONLY' THEN $8::bigint ELSE NULL END
                            )
                            """,
                            *row,
                        )
            finally:
                await connection.close()

    asyncio.run(scenario())


@pytest.mark.integration
def test_runtime_inbox_revision_a_downgrade_rejects_canonical_rows_without_data_loss() -> None:
    """存在 canonical 行时 A → parent 必须失败，并保留版本与原始 payload。"""

    async def scenario() -> None:
        async with temporary_database() as (database, database_url):
            run_alembic("upgrade", REVISION_A, database_url=database_url)
            connection = await connect(database)
            try:
                inbox_id = await connection.fetchval(
                    """
                    INSERT INTO wes_runtime.runtime_inbox (
                        kind, provider_code, event_type, source_event_id,
                        payload_json, payload_hash, payload_schema_version,
                        claim_bucket_key, received_at, status, attempt_count, max_retries
                    ) VALUES (
                        'INTERNAL_EVENT', 'runtime', 'CANONICAL', 'canonical-downgrade',
                        '{"value": 1}'::json, 'hash-canonical', 1,
                        'source:canonical-downgrade', 1000, 'RECEIVED', 0, 5
                    ) RETURNING id
                    """
                )
            finally:
                await connection.close()

            with pytest.raises(subprocess.CalledProcessError) as captured:
                run_alembic("downgrade", REVISION_A_PARENT, database_url=database_url)
            assert "canonical" in captured.value.stderr

            connection = await connect(database)
            try:
                assert await connection.fetchval("SELECT version_num FROM wes_sys.alembic_version") == REVISION_A
                assert (
                    await connection.fetchval(
                        "SELECT payload_json ->> 'value' FROM wes_runtime.runtime_inbox WHERE id = $1",
                        inbox_id,
                    )
                    == "1"
                )
            finally:
                await connection.close()

    asyncio.run(scenario())


@pytest.mark.integration
def test_runtime_inbox_revision_b_preserves_legacy_references_and_refuses_lossy_downgrade() -> None:
    """B 必须映射 legacy 引用；存在引用时禁止通过 downgrade 静默清空。"""

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
                legacy_inbox_id = await _insert_legacy_references(connection)
            finally:
                await connection.close()

            run_alembic("upgrade", REVISION_B, database_url=database_url)
            connection = await connect(database)
            try:
                assert await connection.fetchval("SELECT version_num FROM wes_sys.alembic_version") == REVISION_B
                await _assert_revision_b_schema(
                    connection,
                    expect_indexes=False,
                    legacy_inbox_id=legacy_inbox_id,
                )
            finally:
                await connection.close()

            with pytest.raises(subprocess.CalledProcessError) as captured:
                run_alembic("downgrade", REVISION_A, database_url=database_url)
            assert "would lose identity" in captured.value.stderr

            connection = await connect(database)
            try:
                assert await connection.fetchval("SELECT version_num FROM wes_sys.alembic_version") == REVISION_B
                await _assert_revision_b_schema(
                    connection,
                    expect_indexes=False,
                    legacy_inbox_id=legacy_inbox_id,
                )
            finally:
                await connection.close()

            run_alembic("upgrade", REVISION_C, database_url=database_url)
            connection = await connect(database)
            try:
                assert await connection.fetchval("SELECT version_num FROM wes_sys.alembic_version") == REVISION_C
                await _assert_revision_b_schema(connection, legacy_inbox_id=legacy_inbox_id)
            finally:
                await connection.close()

    asyncio.run(scenario())


@pytest.mark.integration
def test_runtime_inbox_revision_b_empty_database_roundtrip_remains_reversible() -> None:
    """无引用、无 workline session 数据时，B → A → B 仍可安全回环。"""

    async def scenario() -> None:
        async with temporary_database() as (database, database_url):
            run_alembic("upgrade", REVISION_B, database_url=database_url)
            run_alembic("downgrade", REVISION_A, database_url=database_url)
            connection = await connect(database)
            try:
                await _assert_revision_a_downgrade_schema(connection)
            finally:
                await connection.close()

            run_alembic("upgrade", "head", database_url=database_url)
            connection = await connect(database)
            try:
                await _assert_revision_b_schema(connection)
            finally:
                await connection.close()

    asyncio.run(scenario())
