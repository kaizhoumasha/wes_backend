"""InboundEvidence 外键类型迁移的 PostgreSQL 合同。"""

from __future__ import annotations

import asyncio

import pytest

from tests.support.runtime_inbox_postgresql import connect, run_alembic, temporary_database

PREDECESSOR_REVISION = "fe7280088174"
HEAD_REVISION = "f11b613771fa"

_EVIDENCE_FOREIGN_KEYS = (
    ("device_commands", "result_evidence_id"),
    ("inbound_evidence_conflicts", "first_evidence_id"),
    ("material_executions", "last_transition_evidence_id"),
    ("rack_replacement_transport_bindings", "source_evidence_id"),
    ("wms_confirmations", "response_evidence_id"),
)


async def _foreign_key_types(connection) -> dict[tuple[str, str], str]:
    rows = await connection.fetch(
        """
        SELECT table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'wes_biz'
          AND (table_name, column_name) IN (
              ('device_commands', 'result_evidence_id'),
              ('inbound_evidence_conflicts', 'first_evidence_id'),
              ('material_executions', 'last_transition_evidence_id'),
              ('rack_replacement_transport_bindings', 'source_evidence_id'),
              ('wms_confirmations', 'response_evidence_id')
          )
        """
    )
    return {(row["table_name"], row["column_name"]): row["data_type"] for row in rows}


@pytest.mark.integration
def test_evidence_foreign_keys_upgrade_to_bigint_and_downgrade_to_integer() -> None:
    async def scenario() -> None:
        async with temporary_database() as (database, database_url):
            run_alembic("upgrade", PREDECESSOR_REVISION, database_url=database_url)

            connection = await connect(database)
            try:
                assert await _foreign_key_types(connection) == dict.fromkeys(_EVIDENCE_FOREIGN_KEYS, "integer")
            finally:
                await connection.close()

            run_alembic("upgrade", HEAD_REVISION, database_url=database_url)

            connection = await connect(database)
            try:
                assert await _foreign_key_types(connection) == dict.fromkeys(_EVIDENCE_FOREIGN_KEYS, "bigint")
            finally:
                await connection.close()

            run_alembic("downgrade", PREDECESSOR_REVISION, database_url=database_url)

            connection = await connect(database)
            try:
                assert await _foreign_key_types(connection) == dict.fromkeys(_EVIDENCE_FOREIGN_KEYS, "integer")
            finally:
                await connection.close()

    asyncio.run(scenario())
