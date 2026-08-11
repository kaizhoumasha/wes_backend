"""旧工作线插件执行 Schema 退役的 PostgreSQL 集成验收。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.execution_work_item import ExecutionWorkItem
from src.app.runtime.orchestration.models.session import WorklineSession
from src.app.workline.models.workline import WorkLine
from tests.support.runtime_inbox_postgresql import connect, run_alembic, temporary_database

if TYPE_CHECKING:
    import asyncpg


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
    }
)

_MODELS_BY_TABLE = {
    ("wes_biz", "work_lines"): WorkLine,
    ("wes_biz", "workline_sessions"): WorklineSession,
    ("wes_runtime", "execution_sessions"): ExecutionSession,
    ("wes_runtime", "execution_work_items"): ExecutionWorkItem,
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
