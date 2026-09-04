"""PickingTask 发布接收表的迁移合同。"""

from __future__ import annotations

import asyncpg
import pytest

from tests.support.postgresql_catalog import assert_database_head
from tests.support.postgresql_heavy import run_alembic, temporary_database

HEAD_REVISION = "a0f4b56d0f50"


@pytest.mark.asyncio
async def test_picking_task_issued_migration_builds_the_reviewed_postgresql_schema() -> None:
    async with temporary_database() as (_database, database_url):
        run_alembic("upgrade", "head", database_url=database_url)
        connection = await asyncpg.connect(database_url.replace("postgresql+asyncpg", "postgresql", 1))
        try:
            await assert_database_head(connection, HEAD_REVISION)
            columns = await connection.fetch(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'wes_biz' AND table_name = 'picking_tasks'
                ORDER BY ordinal_position
                """
            )
            constraints = {
                row["constraint_name"]: row["definition"]
                for row in await connection.fetch(
                    """
                    SELECT constraint_name, pg_get_constraintdef(pg_constraint.oid) AS definition
                    FROM information_schema.table_constraints
                    JOIN pg_constraint ON pg_constraint.conname = constraint_name
                    WHERE table_schema = 'wes_biz' AND table_name = 'picking_tasks'
                    """
                )
            }
            indexes = {
                row["indexname"]: row["indexdef"]
                for row in await connection.fetch(
                    """
                    SELECT indexname, indexdef
                    FROM pg_indexes
                    WHERE schemaname = 'wes_biz' AND tablename = 'picking_tasks'
                    """
                )
            }
        finally:
            await connection.close()
        run_alembic("check", database_url=database_url)

    assert [tuple(row) for row in columns][-8:] == [
        ("task_id", "character varying", "NO"),
        ("task_type", "character varying", "NO"),
        ("status", "character varying", "NO"),
        ("queue_revision", "bigint", "NO"),
        ("dispatch_sequence", "bigint", "NO"),
        ("not_before_ms", "bigint", "YES"),
        ("issued_at_ms", "bigint", "NO"),
        ("issued_evidence_id", "bigint", "NO"),
    ]
    assert {
        "ux_picking_tasks_task_id",
        "ux_picking_tasks_issued_evidence",
        "fk_picking_tasks_issued_evidence_id_inbound_evidences",
        "ck_picking_tasks_picking_task_status_valid",
        "ck_picking_tasks_picking_task_type_valid",
        "ck_picking_tasks_picking_task_queue_revision_positive",
        "ck_picking_tasks_picking_task_dispatch_sequence_positive",
        "ck_picking_tasks_picking_task_issued_at_positive",
        "ck_picking_tasks_picking_task_not_before_nonnegative",
    }.issubset(constraints)
    assert "QUEUED" in constraints["ck_picking_tasks_picking_task_status_valid"]
    assert constraints["fk_picking_tasks_issued_evidence_id_inbound_evidences"] == (
        "FOREIGN KEY (issued_evidence_id) REFERENCES wes_biz.inbound_evidences(id)"
    )
    assert "ix_picking_tasks_queue" in indexes
    index_definition = indexes["ux_picking_tasks_queued_dispatch_sequence"]
    assert index_definition is not None
    assert "UNIQUE INDEX" in index_definition
    assert "WHERE" in index_definition and "status" in index_definition and "QUEUED" in index_definition
