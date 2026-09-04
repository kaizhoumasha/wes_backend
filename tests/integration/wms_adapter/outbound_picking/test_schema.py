"""PickingTask 发布与 prepare schema 的迁移合同。"""

from __future__ import annotations

import asyncpg
import pytest

from tests.support.postgresql_catalog import assert_database_head
from tests.support.postgresql_heavy import run_alembic, temporary_database

HEAD_REVISION = "ff5d0af61f91"


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
            confirmation_constraints = {
                row["constraint_name"]: row["definition"]
                for row in await connection.fetch(
                    """
                    SELECT constraint_name, pg_get_constraintdef(pg_constraint.oid) AS definition
                    FROM information_schema.table_constraints
                    JOIN pg_constraint ON pg_constraint.conname = constraint_name
                    WHERE table_schema = 'wes_biz' AND table_name = 'wms_confirmations'
                    """
                )
            }
            confirmation_indexes = {
                row["indexname"]: row["indexdef"]
                for row in await connection.fetch(
                    """
                    SELECT indexname, indexdef
                    FROM pg_indexes
                    WHERE schemaname = 'wes_biz' AND tablename = 'wms_confirmations'
                    """
                )
            }
        finally:
            await connection.close()
        run_alembic("check", database_url=database_url)

    assert [tuple(row) for row in columns][-10:] == [
        ("task_id", "character varying", "NO"),
        ("task_type", "character varying", "NO"),
        ("status", "character varying", "NO"),
        ("queue_revision", "bigint", "NO"),
        ("dispatch_sequence", "bigint", "NO"),
        ("not_before_ms", "bigint", "YES"),
        ("issued_at_ms", "bigint", "NO"),
        ("issued_evidence_id", "bigint", "NO"),
        ("workline_id", "integer", "YES"),
        ("line_run_epoch_id", "integer", "YES"),
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
    assert "task_type" in indexes["ix_picking_tasks_queue"]
    assert "not_before_ms" not in indexes["ix_picking_tasks_queue"]
    assert "ux_picking_tasks_active_workline" in indexes
    assert "PREPARING" in indexes["ux_picking_tasks_active_workline"]
    assert "EXECUTING" in indexes["ux_picking_tasks_active_workline"]
    index_definition = indexes["ux_picking_tasks_queued_dispatch_sequence"]
    assert index_definition is not None
    assert "UNIQUE INDEX" in index_definition
    assert "WHERE" in index_definition and "status" in index_definition and "QUEUED" in index_definition
    assert "ck_picking_tasks_picking_task_binding_matches_status" in constraints
    assert "fk_picking_tasks_workline_id_work_lines" in constraints
    assert "fk_picking_tasks_line_run_epoch_id_line_run_epochs" in constraints
    assert "ck_wms_confirmations_wms_confirmation_exactly_one_owner" in confirmation_constraints
    assert "fk_wms_confirmations_bin_execution_id_bin_executions" in confirmation_constraints
    assert "fk_wms_confirmations_picking_task_id_picking_tasks" in confirmation_constraints
    assert "ux_wms_confirmations_picking_task_operation" in confirmation_indexes
