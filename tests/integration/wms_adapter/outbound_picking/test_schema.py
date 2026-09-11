"""PickingTask 发布与 prepare schema 的迁移合同。"""

from __future__ import annotations

import subprocess

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.app.execution.models import InboundEvidence, InboundEvidenceKind
from src.app.workline.models import WorkLine
from src.utils.timezone import timezone
from tests.support.postgresql_catalog import assert_database_head
from tests.support.postgresql_heavy import run_alembic, temporary_database

HEAD_REVISION = "f7cf0cd8c6d4"


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
            workline_owner_column = await connection.fetchrow(
                """
                SELECT data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'wes_biz' AND table_name = 'wms_confirmations'
                  AND column_name = 'workline_id'
                """
            )
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

    assert [tuple(row) for row in columns][-15:] == [
        ("task_id", "character varying", "NO"),
        ("task_type", "character varying", "NO"),
        ("status", "character varying", "NO"),
        ("queue_revision", "bigint", "NO"),
        ("dispatch_sequence", "bigint", "NO"),
        ("not_before_ms", "bigint", "YES"),
        ("issued_at_ms", "bigint", "NO"),
        ("issued_evidence_id", "bigint", "NO"),
        ("workline_id", "bigint", "YES"),
        ("last_applied_plan_revision", "bigint", "NO"),
        ("target_rack_id", "character varying", "YES"),
        ("target_rack_face", "character varying", "YES"),
        ("initial_plan_evidence_id", "bigint", "YES"),
        ("last_plan_evidence_id", "bigint", "YES"),
        ("plan_blocked_evidence_id", "bigint", "YES"),
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
    assert "fk_picking_tasks_line_run_epoch_id_line_run_epochs" not in constraints
    assert "ck_wms_confirmations_wms_confirmation_exactly_one_owner" in confirmation_constraints
    assert "fk_wms_confirmations_bin_execution_id_bin_executions" not in confirmation_constraints
    assert "fk_wms_confirmations_picking_task_id_picking_tasks" in confirmation_constraints
    assert "ux_wms_confirmations_picking_task_operation" not in confirmation_indexes
    definition = confirmation_indexes["ux_wms_confirmations_picking_task_prepare"]
    assert "UNIQUE INDEX" in definition
    assert "picking_task_id IS NOT NULL" in definition
    assert "outbound.picking_task.prepare@v1" in definition
    assert "SUPERSEDED" in definition
    assert "SUPERSEDED" in confirmation_constraints["ck_wms_confirmations_wms_confirmation_status_valid"]
    assert tuple(workline_owner_column) == ("bigint", "YES")
    assert confirmation_constraints["fk_wms_confirmations_workline_id_work_lines"] == (
        "FOREIGN KEY (workline_id) REFERENCES wes_biz.work_lines(id)"
    )
    owner_check = confirmation_constraints["ck_wms_confirmations_wms_confirmation_exactly_one_owner"]
    for owner in ("material_execution_id", "picking_task_id", "workline_id"):
        assert f"{owner} IS NOT NULL" in owner_check
    assert "= 1" in owner_check
    assert "ix_wes_biz_wms_confirmations_workline_id" in confirmation_indexes


@pytest.mark.asyncio
async def test_evidence_workline_id_migration_preserves_rows_and_rejects_lossy_downgrade() -> None:
    async with temporary_database() as (_database, database_url):
        run_alembic("upgrade", "3abf401aebaa", database_url=database_url)
        engine = create_async_engine(database_url)
        try:
            async with async_sessionmaker(engine).begin() as db:
                db.add_all(
                    [
                        WorkLine(id=42, line_code="SMALL", line_name="Small owner", line_type="AUTO", is_active=False),
                        WorkLine(
                            id=347454468883008,
                            line_code="LARGE",
                            line_name="Large owner",
                            line_type="AUTO",
                            is_active=False,
                        ),
                    ]
                )
                await db.flush()
                db.add(
                    InboundEvidence(
                        id=43,
                        kind=InboundEvidenceKind.DEVICE_EVENT,
                        source_identity="bigint-migration",
                        payload_digest="a" * 64,
                        normalized_payload={"preserved": True},
                        received_at=timezone.now_for_db(),
                        workline_id=42,
                        device_code="SCANNER",
                    )
                )
        finally:
            await engine.dispose()
        run_alembic("upgrade", HEAD_REVISION, database_url=database_url)
        connection = await asyncpg.connect(database_url.replace("postgresql+asyncpg", "postgresql", 1))
        try:
            assert (
                await connection.fetchval(
                    "SELECT data_type FROM information_schema.columns WHERE table_schema='wes_biz' "
                    "AND table_name='inbound_evidences' AND column_name='workline_id'"
                )
                == "bigint"
            )
            row = await connection.fetchrow(
                "SELECT workline_id, normalized_payload->>'preserved' AS preserved "
                "FROM wes_biz.inbound_evidences WHERE id=43"
            )
            assert tuple(row) == (42, "true")
            await connection.execute("UPDATE wes_biz.inbound_evidences SET workline_id=347454468883008 WHERE id=43")
            with pytest.raises(asyncpg.ForeignKeyViolationError):
                await connection.execute("UPDATE wes_biz.inbound_evidences SET workline_id=999 WHERE id=43")
        finally:
            await connection.close()
        with pytest.raises(subprocess.CalledProcessError):
            run_alembic("downgrade", "3abf401aebaa", database_url=database_url)
        connection = await asyncpg.connect(database_url.replace("postgresql+asyncpg", "postgresql", 1))
        try:
            await assert_database_head(connection, HEAD_REVISION)
            assert (
                await connection.fetchval("SELECT workline_id FROM wes_biz.inbound_evidences WHERE id=43")
                == 347454468883008
            )
            await connection.execute("UPDATE wes_biz.inbound_evidences SET workline_id=42 WHERE id=43")
        finally:
            await connection.close()
        run_alembic("downgrade", "3abf401aebaa", database_url=database_url)
        run_alembic("upgrade", HEAD_REVISION, database_url=database_url)
