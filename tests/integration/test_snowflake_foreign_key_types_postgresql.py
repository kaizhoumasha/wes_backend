"""迁移后所有外键列与引用列使用相同的 PostgreSQL 类型。"""

from __future__ import annotations

import asyncpg
import pytest

from tests.support.postgresql_heavy import migrated_database

pytestmark = pytest.mark.integration

_FOREIGN_KEY_TYPES = """
SELECT
    source_namespace.nspname || '.' || source_table.relname || '.' || source_column.attname AS source,
    target_namespace.nspname || '.' || target_table.relname || '.' || target_column.attname AS target,
    source_column.atttypid::regtype::text AS source_type,
    target_column.atttypid::regtype::text AS target_type,
    source_column.atttypmod AS source_typmod,
    target_column.atttypmod AS target_typmod
FROM pg_constraint AS fk_constraint
JOIN pg_class AS source_table ON source_table.oid = fk_constraint.conrelid
JOIN pg_namespace AS source_namespace ON source_namespace.oid = source_table.relnamespace
JOIN pg_class AS target_table ON target_table.oid = fk_constraint.confrelid
JOIN pg_namespace AS target_namespace ON target_namespace.oid = target_table.relnamespace
JOIN LATERAL unnest(fk_constraint.conkey) WITH ORDINALITY AS source_key(attnum, ordinal) ON true
JOIN LATERAL unnest(fk_constraint.confkey) WITH ORDINALITY AS target_key(attnum, ordinal)
    ON target_key.ordinal = source_key.ordinal
JOIN pg_attribute AS source_column
    ON source_column.attrelid = source_table.oid AND source_column.attnum = source_key.attnum
JOIN pg_attribute AS target_column
    ON target_column.attrelid = target_table.oid AND target_column.attnum = target_key.attnum
WHERE fk_constraint.contype = 'f'
    AND source_namespace.nspname IN ('wes_biz', 'wes_runtime', 'wes_sys')
ORDER BY source
"""

_MIGRATED_REFERENCES = {
    "wes_biz.inbound_evidences.material_execution_id",
    "wes_biz.wms_confirmations.material_execution_id",
    "wes_biz.device_commands.material_execution_id",
    "wes_biz.workline_sessions.workline_id",
    "wes_biz.workline_timelines.session_id",
    "wes_biz.workline_timelines.workline_id",
    "wes_biz.workline_timelines.related_command_id",
}


@pytest.mark.asyncio
async def test_migrated_foreign_key_types_match_referenced_columns() -> None:
    async with migrated_database() as (url, _sessions):
        connection = await asyncpg.connect(url.replace("postgresql+asyncpg", "postgresql", 1))
        try:
            rows = await connection.fetch(_FOREIGN_KEY_TYPES)
            assert {row["source"] for row in rows} >= _MIGRATED_REFERENCES
            assert [
                (row["source"], row["source_type"], row["target_type"])
                for row in rows
                if (row["source_type"], row["source_typmod"]) != (row["target_type"], row["target_typmod"])
            ] == []
            assert {row["source_type"] for row in rows if row["source"] in _MIGRATED_REFERENCES} == {"bigint"}
        finally:
            await connection.close()
