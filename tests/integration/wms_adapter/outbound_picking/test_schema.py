"""PickingTask 发布与 prepare schema 的迁移合同。"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import asyncpg
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.app.workline.models import WorkLine
from src.utils.timezone import timezone
from tests.support.postgresql_catalog import assert_database_head
from tests.support.postgresql_heavy import run_alembic, temporary_database

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

HEAD_REVISION = "496bdbaaff26"


@pytest.mark.asyncio
async def test_picking_task_issued_migration_builds_the_reviewed_postgresql_schema() -> None:
    async with temporary_database() as (_database, database_url):
        run_alembic("upgrade", HEAD_REVISION, database_url=database_url)
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
            member_columns = {
                table_name: {
                    row["column_name"]: (row["data_type"], row["is_nullable"])
                    for row in await connection.fetch(
                        """
                        SELECT column_name, data_type, is_nullable
                        FROM information_schema.columns
                        WHERE table_schema = 'wes_biz' AND table_name = $1
                        """,
                        table_name,
                    )
                }
                for table_name in ("direct_pick_executions", "picking_task_bin_source_racks")
            }
            evidence_columns = {
                row["column_name"]: (row["data_type"], row["is_nullable"])
                for row in await connection.fetch(
                    """
                    SELECT column_name, data_type, is_nullable
                    FROM information_schema.columns
                    WHERE table_schema = 'wes_biz' AND table_name = 'inbound_evidences'
                    """
                )
            }
            evidence_constraints = {
                row["constraint_name"]: row["definition"]
                for row in await connection.fetch(
                    """
                    SELECT constraint_name, pg_get_constraintdef(pg_constraint.oid) AS definition
                    FROM information_schema.table_constraints
                    JOIN pg_constraint ON pg_constraint.conname = constraint_name
                    WHERE table_schema = 'wes_biz' AND table_name = 'inbound_evidences'
                    """
                )
            }
            evidence_indexes = {
                row["indexname"]: row["indexdef"]
                for row in await connection.fetch(
                    """
                    SELECT indexname, indexdef
                    FROM pg_indexes
                    WHERE schemaname = 'wes_biz' AND tablename = 'inbound_evidences'
                    """
                )
            }
            binding_columns = {
                row["column_name"]: (row["data_type"], row["is_nullable"])
                for row in await connection.fetch(
                    """
                    SELECT column_name, data_type, is_nullable
                    FROM information_schema.columns
                    WHERE table_schema = 'wes_biz' AND table_name = 'transport_decision_bindings'
                    """
                )
            }
            binding_indexes = {
                row["indexname"]: row["indexdef"]
                for row in await connection.fetch(
                    """
                    SELECT indexname, indexdef
                    FROM pg_indexes
                    WHERE schemaname = 'wes_biz' AND tablename = 'transport_decision_bindings'
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
    assert [tuple(row) for row in columns][-16:] == [
        ("task_id", "character varying", "NO"),
        ("task_type", "character varying", "NO"),
        ("status", "character varying", "NO"),
        ("queue_revision", "bigint", "NO"),
        ("dispatch_sequence", "bigint", "NO"),
        ("not_before_ms", "bigint", "YES"),
        ("issued_at_ms", "bigint", "NO"),
        ("issued_evidence_id", "bigint", "NO"),
        ("workline_id", "bigint", "NO"),
        ("last_applied_plan_revision", "bigint", "NO"),
        ("target_rack_id", "character varying", "YES"),
        ("target_rack_face", "character varying", "YES"),
        ("initial_plan_evidence_id", "bigint", "YES"),
        ("last_plan_evidence_id", "bigint", "YES"),
        ("plan_blocked_evidence_id", "bigint", "YES"),
        ("archived_at", "timestamp without time zone", "YES"),
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
    assert "CANCELLED" in constraints["ck_picking_tasks_picking_task_status_valid"]
    assert constraints["fk_picking_tasks_issued_evidence_id_inbound_evidences"] == (
        "FOREIGN KEY (issued_evidence_id) REFERENCES wes_biz.inbound_evidences(id)"
    )
    assert "ix_picking_tasks_queue" in indexes
    assert "(workline_id, task_type, dispatch_sequence, id)" in indexes["ix_picking_tasks_queue"]
    assert "task_type" in indexes["ix_picking_tasks_queue"]
    assert "not_before_ms" not in indexes["ix_picking_tasks_queue"]
    assert "ux_picking_tasks_active_workline" in indexes
    assert "PREPARING" in indexes["ux_picking_tasks_active_workline"]
    assert "EXECUTING" in indexes["ux_picking_tasks_active_workline"]
    index_definition = indexes["ux_picking_tasks_queued_dispatch_sequence"]
    assert index_definition is not None
    assert "UNIQUE INDEX" in index_definition
    assert "WHERE" in index_definition and "status" in index_definition and "QUEUED" in index_definition
    assert "ck_picking_tasks_picking_task_binding_matches_status" not in constraints
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
    for columns in member_columns.values():
        assert columns["cancelled_evidence_id"] == ("bigint", "YES")
    assert evidence_columns["picking_task_id"] == ("bigint", "YES")
    assert evidence_constraints["fk_inbound_evidences_picking_task_id_picking_tasks"] == (
        "FOREIGN KEY (picking_task_id) REFERENCES wes_biz.picking_tasks(id)"
    )
    evidence_timeline = evidence_indexes["ix_inbound_evidences_picking_task_timeline"]
    assert "(picking_task_id, received_at, id)" in evidence_timeline
    for constraint in (
        "ck_inbound_evidences_inbound_evidence_wms_identity_required",
        "ck_inbound_evidences_inbound_evidence_device_identity_required",
    ):
        assert constraint in evidence_constraints
    transport_identity_checks = [
        definition
        for definition in evidence_constraints.values()
        if "TRANSPORT_RESULT" in definition and "transport_task_id" in definition
    ]
    assert len(transport_identity_checks) == 1
    assert any(
        "TRANSPORT_RESULT" in definition and "device_code" in definition and "operation_id" in definition
        for definition in evidence_constraints.values()
    )
    assert binding_columns["picking_task_id"] == ("bigint", "YES")
    binding_task_step = binding_indexes["ix_transport_decision_bindings_task_step"]
    assert "(workline_id, picking_task_id, step)" in binding_task_step
    assert "(picking_task_id)" in binding_indexes["ix_transport_decision_bindings_picking_task"]


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
            # 反射当前(旧)版本的 inbound_evidences 表结构再插入，而不是用当前 ORM 模型：
            # 模型会随后续迁移（如 picking_task_id）持续演进，若用模型直接插入，
            # 未来任何新增列都会让这条构造在旧版本 schema 上插入失败。
            async with engine.begin() as conn:

                def _reflect_evidence_table(sync_conn: Connection) -> sa.Table:
                    return sa.Table("inbound_evidences", sa.MetaData(), autoload_with=sync_conn, schema="wes_biz")

                evidence_table = await conn.run_sync(_reflect_evidence_table)
                await conn.execute(
                    evidence_table.insert().values(
                        id=43,
                        kind="DEVICE_EVENT",
                        source_identity="bigint-migration",
                        payload_digest="a" * 64,
                        normalized_payload={"preserved": True},
                        received_at=timezone.now_for_db(),
                        created_at=timezone.now_for_db(),
                        workline_id=42,
                        device_code="SCANNER",
                        apply_status="PENDING",
                        decision_attempt_count=0,
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
