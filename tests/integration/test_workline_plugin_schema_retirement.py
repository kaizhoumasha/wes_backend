"""旧工作线插件执行 Schema 退役的 PostgreSQL 集成验收。"""

from __future__ import annotations

import asyncio

import asyncpg
import pytest

from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.execution_work_item import ExecutionWorkItem
from src.app.runtime.orchestration.models.diagnostic import WorklineDiagnostic
from src.app.runtime.orchestration.models.runtime_hold import RuntimeHold
from src.app.runtime.orchestration.models.session import WorklineSession
from src.app.workline.models.workline import WorkLine
from tests.support.runtime_inbox_postgresql import connect, run_alembic, temporary_database

_RETIRED_COLUMNS: dict[tuple[str, str], frozenset[str]] = {
    ("wes_biz", "work_lines"): frozenset(
        {
            "plugin_key",
            "contract_version",
            "active_plugin_binding_id",
            "active_plugin_binding_version",
            "active_plugin_config_hash",
            "active_plugin_index_digest",
            "active_plugin_provider_requirements_json",
            "active_plugin_port_requirements_json",
        }
    ),
    ("wes_biz", "workline_sessions"): frozenset(
        {
            "plugin_key",
            "contract_version",
            "plugin_binding_id",
            "plugin_binding_version",
            "plugin_config_hash",
            "plugin_index_digest",
            "plugin_state_json",
            "plugin_state_version",
        }
    ),
    ("wes_runtime", "execution_sessions"): frozenset(
        {
            "plugin_key",
            "manifest_version",
            "plugin_binding_id",
            "plugin_binding_version",
            "plugin_config_hash",
            "plugin_index_digest",
            "plugin_state_json",
            "plugin_state_version",
        }
    ),
    ("wes_runtime", "execution_work_items"): frozenset(
        {
            "plugin_key",
            "manifest_version",
            "plugin_binding_id",
            "plugin_binding_version",
            "plugin_config_hash",
            "plugin_index_digest",
            "plugin_state_json",
            "plugin_state_version",
        }
    ),
    ("wes_biz", "workline_diagnostics"): frozenset({"plugin_key"}),
    ("wes_biz", "runtime_holds"): frozenset({"plugin_key", "contract_version"}),
}

_RETIRED_TABLES = frozenset(
    {
        ("wes_biz", "smt_inbound_handoff_demands"),
        ("wes_biz", "smt_inbound_handoff_source_items"),
        ("wes_biz", "workline_plugin_bindings"),
        ("wes_runtime", "wms_conveyor_batch_members"),
    }
)

_RETIRED_FOREIGN_KEYS = frozenset(
    {
        "fk_work_lines_active_plugin_binding",
        "fk_workline_sessions_plugin_binding",
        "fk_execution_sessions_plugin_binding",
        "fk_execution_work_items_plugin_binding",
    }
)

_RETIRED_INDEXES = frozenset(
    {
        "ix_wes_biz_work_lines_plugin_key",
        "ix_wes_biz_work_lines_active_plugin_binding_id",
        "ix_wes_biz_workline_sessions_plugin_key",
        "ix_wes_biz_workline_sessions_plugin_binding_id",
        "ix_wes_runtime_execution_sessions_plugin_key",
        "ix_wes_runtime_execution_sessions_plugin_binding_id",
        "ix_wes_runtime_execution_work_items_plugin_key",
        "ix_wes_runtime_execution_work_items_plugin_binding_id",
        "ix_wes_biz_runtime_holds_plugin_key",
    }
)

_MODELS_BY_TABLE = {
    ("wes_biz", "work_lines"): WorkLine,
    ("wes_biz", "workline_sessions"): WorklineSession,
    ("wes_runtime", "execution_sessions"): ExecutionSession,
    ("wes_runtime", "execution_work_items"): ExecutionWorkItem,
    ("wes_biz", "workline_diagnostics"): WorklineDiagnostic,
    ("wes_biz", "runtime_holds"): RuntimeHold,
}

_NG_REASON_SOURCE_CONSTRAINTS = {
    "runtime_holds": "ck_runtime_holds_ngreasonsource",
    "ng_return_items": "ck_ng_return_items_ngreturnitemngreasonsource",
}


async def _column_names(connection: asyncpg.Connection, *, schema: str, table: str) -> set[str]:
    rows = await connection.fetch(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = $1 AND table_name = $2
        """,
        schema,
        table,
    )
    return {str(row["column_name"]) for row in rows}


async def _foreign_key_names(connection: asyncpg.Connection) -> set[str]:
    rows = await connection.fetch(
        """
        SELECT constraint_name
        FROM information_schema.table_constraints
        WHERE constraint_schema = ANY($1::text[])
          AND constraint_type = 'FOREIGN KEY'
        """,
        ["wes_biz", "wes_runtime"],
    )
    return {str(row["constraint_name"]) for row in rows}


async def _index_names(connection: asyncpg.Connection) -> set[str]:
    rows = await connection.fetch(
        """
        SELECT indexname
        FROM pg_indexes
        WHERE schemaname = ANY($1::text[])
        """,
        ["wes_biz", "wes_runtime"],
    )
    return {str(row["indexname"]) for row in rows}


async def _ng_reason_source_check_definitions(
    connection: asyncpg.Connection,
    *,
    table: str,
) -> dict[str, str]:
    rows = await connection.fetch(
        """
        SELECT constraint_row.conname,
               pg_get_constraintdef(constraint_row.oid) AS definition
        FROM pg_constraint AS constraint_row
        JOIN pg_class AS table_row ON table_row.oid = constraint_row.conrelid
        JOIN pg_namespace AS namespace_row ON namespace_row.oid = table_row.relnamespace
        WHERE namespace_row.nspname = 'wes_biz'
          AND table_row.relname = $1
          AND constraint_row.contype = 'c'
          AND pg_get_constraintdef(constraint_row.oid) LIKE '%ng_reason_source%'
        """,
        table,
    )
    return {str(row["conname"]): str(row["definition"]) for row in rows}


async def _insert_ng_reason_source_rows(connection: asyncpg.Connection) -> tuple[int, int]:
    workline_id = await connection.fetchval(
        """
        INSERT INTO wes_biz.work_lines (
            created_at, line_code, line_name, line_type, is_active
        ) VALUES (
            CURRENT_TIMESTAMP, 'NG-SOURCE-CHECK', 'NG source constraint check', 'AUTO', FALSE
        )
        RETURNING id
        """
    )
    session_id = await connection.fetchval(
        """
        INSERT INTO wes_biz.workline_sessions (
            created_at, session_code, workline_id, run_mode, status
        ) VALUES (
            CURRENT_TIMESTAMP, 'NG-SOURCE-CHECK', $1, 'AUTO', 'NEW'
        )
        RETURNING id
        """,
        workline_id,
    )
    hold_id = await connection.fetchval(
        """
        INSERT INTO wes_biz.runtime_holds (
            created_at, hold_type, status, blocking, workline_id, session_id,
            source_kind, source_reason, source_idempotency_key, ng_reason_source
        ) VALUES (
            CURRENT_TIMESTAMP, 'MANUAL_HOLD', 'OPEN', TRUE, $1, $2,
            'INTERNAL_EVENT', 'NG source constraint check', 'ng-source-check-hold', 'DEVICE_ERROR'
        )
        RETURNING id
        """,
        workline_id,
        session_id,
    )
    item_id = await connection.fetchval(
        """
        INSERT INTO wes_biz.ng_return_items (
            created_at, source_workline_id, source_session_id, material_identity_key,
            material_identity_json, physical_handoff_evidence_json, disposition,
            ng_reason_source, ng_reason_code, ng_reason_label,
            created_from_runtime_hold_id, status
        ) VALUES (
            CURRENT_TIMESTAMP, $1, $2, 'ng-source-check-material',
            '{}'::json, '{}'::json, 'RETURN_TO_NG',
            'DEVICE_ERROR', 'DEVICE_ERROR_CHECK', 'Device error check',
            $3, 'WAITING_REWORK'
        )
        RETURNING id
        """,
        workline_id,
        session_id,
        hold_id,
    )
    return int(hold_id), int(item_id)


@pytest.mark.integration
def test_upgrade_head_retires_workline_plugin_execution_schema() -> None:
    """空库升级到 head 后，旧插件执行闭包不得保留任何 Schema 所有权。"""

    async def scenario() -> None:
        async with temporary_database() as (database, database_url):
            run_alembic("upgrade", "head", database_url=database_url)
            connection = await connect(database)
            try:
                retired_tables = await connection.fetch(
                    """
                    SELECT table_schema, table_name
                    FROM information_schema.tables
                    WHERE table_schema = ANY($1::text[])
                    """,
                    ["wes_biz", "wes_runtime"],
                )
                table_identities = {(str(row["table_schema"]), str(row["table_name"])) for row in retired_tables}
                assert _RETIRED_TABLES.isdisjoint(table_identities)

                for table_identity, retired_columns in _RETIRED_COLUMNS.items():
                    schema, table = table_identity
                    database_columns = await _column_names(connection, schema=schema, table=table)
                    assert retired_columns.isdisjoint(database_columns), table_identity

                    model_columns = set(_MODELS_BY_TABLE[table_identity].__table__.columns.keys())
                    assert retired_columns.isdisjoint(model_columns), _MODELS_BY_TABLE[table_identity].__name__

                assert _RETIRED_FOREIGN_KEYS.isdisjoint(await _foreign_key_names(connection))
                assert _RETIRED_INDEXES.isdisjoint(await _index_names(connection))
            finally:
                await connection.close()

    asyncio.run(scenario())


@pytest.mark.integration
def test_upgrade_head_restricts_ng_reason_source_to_shared_runtime_sources() -> None:
    """两张持久表只接受 DEVICE_ERROR/RUNTIME/MANUAL，拒绝退役 PLUGIN。"""

    async def scenario() -> None:
        async with temporary_database() as (database, database_url):
            run_alembic("upgrade", "head", database_url=database_url)
            connection = await connect(database)
            try:
                hold_id, item_id = await _insert_ng_reason_source_rows(connection)
                for table, row_id in (("ng_return_items", item_id), ("runtime_holds", hold_id)):
                    definitions = await _ng_reason_source_check_definitions(connection, table=table)
                    assert set(definitions) == {_NG_REASON_SOURCE_CONSTRAINTS[table]}, (table, definitions)
                    definition = next(iter(definitions.values()))
                    assert "DEVICE_ERROR" in definition
                    assert "RUNTIME" in definition
                    assert "MANUAL" in definition
                    assert "PLUGIN" not in definition, (table, definitions)

                    for source in ("DEVICE_ERROR", "RUNTIME", "MANUAL"):
                        await connection.execute(
                            f'UPDATE wes_biz."{table}" SET ng_reason_source = $1 WHERE id = $2',
                            source,
                            row_id,
                        )
                    for unsupported_source in ("PLUGIN", "UNSUPPORTED"):
                        with pytest.raises(asyncpg.CheckViolationError):
                            await connection.execute(
                                f'UPDATE wes_biz."{table}" SET ng_reason_source = $1 WHERE id = $2',
                                unsupported_source,
                                row_id,
                            )
            finally:
                await connection.close()

    asyncio.run(scenario())
