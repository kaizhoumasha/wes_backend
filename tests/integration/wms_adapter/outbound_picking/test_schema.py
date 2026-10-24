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


@pytest.mark.asyncio
async def test_business_type_migration_preserves_existing_outbound_and_checks_new_values() -> None:
    from src.app.execution.models import InboundEvidence, InboundEvidenceKind
    from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskType
    from src.app.workline.models import LineType

    async with temporary_database() as (_database, database_url):
        run_alembic("upgrade", "a8c5e2107b94", database_url=database_url)
        run_alembic("upgrade", "f0c2423ac98a", database_url=database_url)
        engine = create_async_engine(database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions.begin() as db:
                line = WorkLine(line_code="BUSINESS-TYPE", line_name="任务类型迁移", line_type=LineType.MANUAL)
                evidence = InboundEvidence(
                    kind=InboundEvidenceKind.WMS_EVENT,
                    source_identity="BUSINESS-TYPE-MIGRATION",
                    operation="outbound.picking_task.issued@v1",
                    operation_id="019f33f0-58d7-7b4d-a23a-1b90aa5d4473",
                    payload_digest="a" * 64,
                    normalized_payload={"data": {}},
                    received_at=timezone.now_for_db(),
                )
                db.add_all([line, evidence])
                await db.flush()
                db.add(
                    PickingTask(
                        task_id="BUSINESS-TYPE",
                        task_type=PickingTaskType.MANUAL,
                        queue_revision=1,
                        dispatch_sequence=1,
                        issued_at_ms=1,
                        issued_evidence_id=evidence.id,
                        workline_id=line.id,
                    )
                )
        finally:
            await engine.dispose()
        run_alembic("downgrade", "f72bd038e915", database_url=database_url)
        run_alembic("upgrade", "a8c5e2107b94", database_url=database_url)
        connection = await asyncpg.connect(database_url.replace("postgresql+asyncpg", "postgresql", 1))
        try:
            assert await connection.fetchval("SELECT business_type FROM wes_biz.picking_tasks") == "outbound"
            await connection.execute("UPDATE wes_biz.picking_tasks SET business_type = 'inbound'")
            for value in ("unknown", "INBOUND", None):
                with pytest.raises((asyncpg.CheckViolationError, asyncpg.NotNullViolationError)):
                    await connection.execute("UPDATE wes_biz.picking_tasks SET business_type = $1", value)
            with pytest.raises(asyncpg.CheckViolationError):
                await connection.execute("UPDATE wes_biz.picking_tasks SET task_type = 'AUTO'")
            await assert_database_head(connection, "a8c5e2107b94")
        finally:
            await connection.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "starting_revisions", [(), ("a8c5e2107b94",), ("f0c2423ac98a",), ("a8c5e2107b94", "f0c2423ac98a")]
)
async def test_picking_task_merge_converges_heads_without_rewriting_existing_data(starting_revisions) -> None:
    from datetime import datetime

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from src.app.execution.models import InboundEvidence, InboundEvidenceKind
    from src.app.workline.models import LineType

    merge_revision = "c6e209af714b"
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    assert set(scripts.get_revision(merge_revision).down_revision) == {"a8c5e2107b94", "f0c2423ac98a"}
    async with temporary_database() as (_database, database_url):
        run_alembic("upgrade", "f72bd038e915", database_url=database_url)
        engine = create_async_engine(database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        created_at = datetime(2026, 10, 19, 10)
        try:
            async with sessions.begin() as db:
                line = WorkLine(line_code="MERGE-HEADS", line_name="迁移合并验证", line_type=LineType.MANUAL)
                evidence = InboundEvidence(
                    kind=InboundEvidenceKind.WMS_EVENT,
                    source_identity="MERGE-HEADS-ISSUED",
                    operation="outbound.picking_task.issued@v1",
                    operation_id="019f33f0-58d7-7b4d-a23a-1b90aa5d4473",
                    payload_digest="a" * 64,
                    normalized_payload={"data": {}},
                    received_at=created_at,
                )
                db.add_all([line, evidence])
                await db.flush()
                await db.execute(
                    sa.text(
                        "INSERT INTO wes_biz.picking_tasks "
                        "(id, task_id, task_type, status, queue_revision, dispatch_sequence, issued_at_ms, "
                        "issued_evidence_id, workline_id, created_at) "
                        "VALUES (1, 'MERGE-HEADS', 'MANUAL', 'CANCELLED', 1, 1, 1, :evidence, :line, :created)"
                    ),
                    {"evidence": evidence.id, "line": line.id, "created": created_at},
                )
        finally:
            await engine.dispose()

        async def inspect_database():
            connection = await asyncpg.connect(database_url.replace("postgresql+asyncpg", "postgresql", 1))
            try:
                row = dict(await connection.fetchrow("SELECT * FROM wes_biz.picking_tasks WHERE id=1"))
                columns = await connection.fetch(
                    "SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns "
                    "WHERE table_schema='wes_biz' AND table_name='picking_tasks' ORDER BY ordinal_position"
                )
                constraints = await connection.fetch(
                    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid='wes_biz.picking_tasks'::regclass ORDER BY conname"
                )
                versions = await connection.fetch(
                    "SELECT version_num FROM wes_sys.alembic_version ORDER BY version_num"
                )
                return row, columns, constraints, [v[0] for v in versions]
            finally:
                await connection.close()

        original_row = (await inspect_database())[0]
        for revision in starting_revisions:
            run_alembic("upgrade", revision, database_url=database_url)
        before = await inspect_database()
        run_alembic("upgrade", merge_revision, database_url=database_url)
        after = await inspect_database()
        assert after[3] == [merge_revision]
        assert after[0]["business_type"] == "outbound"
        assert after[0]["cancelled_at"] == created_at
        assert {k: v for k, v in after[0].items() if k not in {"business_type", "cancelled_at"}} == original_row
        if len(starting_revisions) == 2:
            assert before[3] == ["a8c5e2107b94", "f0c2423ac98a"]
            assert before[:3] == after[:3]
        run_alembic("upgrade", merge_revision, database_url=database_url)
        assert await inspect_database() == after


@pytest.mark.asyncio
async def test_task_cancel_projection_migration_preserves_only_confirmed_cancellation() -> None:
    now = timezone.now_for_db()
    cases = [
        ("CANCELLED", None, None, True),
        ("ARCHIVED", "APPLIED", "TASK", True),
        ("ARCHIVED", "RECONCILING", "TASK", False),
        ("ARCHIVED", "APPLIED", "PLAN_MEMBERS", False),
        ("ARCHIVED", None, None, False),
        ("EXECUTION_COMPLETED", None, None, False),
    ]
    async with temporary_database() as (_database, database_url):
        run_alembic("upgrade", "f72bd038e915", database_url=database_url)
        engine = create_async_engine(database_url)
        try:
            async with async_sessionmaker(engine).begin() as db:
                db.add(WorkLine(id=42, line_code="CANCEL-MIGRATION", line_name="Cancel", line_type="MANUAL"))
            async with engine.begin() as conn:

                def reflect(sync_conn: Connection) -> tuple[sa.Table, sa.Table]:
                    metadata = sa.MetaData()
                    return (
                        sa.Table("inbound_evidences", metadata, autoload_with=sync_conn, schema="wes_biz"),
                        sa.Table("picking_tasks", metadata, autoload_with=sync_conn, schema="wes_biz"),
                    )

                evidences, tasks = await conn.run_sync(reflect)
                for index, (status, apply_status, scope, _expected) in enumerate(cases, 1):
                    await conn.execute(
                        evidences.insert().values(
                            id=index,
                            created_at=now,
                            kind="WMS_EVENT",
                            source_identity=f"issued-{index}",
                            operation="outbound.picking_task.issued@v1",
                            operation_id=f"issued-{index}",
                            normalized_payload={},
                            payload_digest="a" * 64,
                            received_at=now,
                            apply_status="APPLIED",
                            decision_attempt_count=0,
                        )
                    )
                    await conn.execute(
                        tasks.insert().values(
                            id=10 + index,
                            created_at=now,
                            updated_at=now,
                            task_id=f"PICK-{index}",
                            task_type="MANUAL",
                            status=status,
                            queue_revision=1,
                            dispatch_sequence=index,
                            issued_at_ms=1,
                            issued_evidence_id=index,
                            workline_id=42,
                            archived_at=now if status == "ARCHIVED" else None,
                        )
                    )
                    if apply_status is not None:
                        await conn.execute(
                            evidences.insert().values(
                                id=100 + index,
                                created_at=now,
                                kind="WMS_EVENT",
                                source_identity=f"cancel-{index}",
                                operation="outbound.picking_task.cancel@v1",
                                operation_id=f"cancel-{index}",
                                normalized_payload={"data": {"cancel_scope": scope}},
                                payload_digest="b" * 64,
                                received_at=now,
                                processed_at=now,
                                apply_status=apply_status,
                                picking_task_id=10 + index,
                                decision_attempt_count=0,
                            )
                        )
        finally:
            await engine.dispose()
        connection = await asyncpg.connect(database_url.replace("postgresql+asyncpg", "postgresql", 1))
        try:
            original_tasks = await connection.fetch(
                "SELECT to_jsonb(t) AS row FROM wes_biz.picking_tasks t ORDER BY id"
            )
            original_evidence = await connection.fetch(
                "SELECT to_jsonb(e) AS row FROM wes_biz.inbound_evidences e ORDER BY id"
            )
        finally:
            await connection.close()
        run_alembic("upgrade", "f0c2423ac98a", database_url=database_url)
        run_alembic("downgrade", "f72bd038e915", database_url=database_url)
        run_alembic("upgrade", "f0c2423ac98a", database_url=database_url)
        connection = await asyncpg.connect(database_url.replace("postgresql+asyncpg", "postgresql", 1))
        try:
            assert (
                await connection.fetch(
                    "SELECT to_jsonb(t) - 'cancelled_at' AS row FROM wes_biz.picking_tasks t ORDER BY id"
                )
                == original_tasks
            )
            assert (
                await connection.fetch("SELECT to_jsonb(e) AS row FROM wes_biz.inbound_evidences e ORDER BY id")
                == original_evidence
            )
            assert [
                row["cancelled_at"]
                for row in await connection.fetch("SELECT cancelled_at FROM wes_biz.picking_tasks ORDER BY id")
            ] == [now if expected else None for _status, _apply, _scope, expected in cases]
            await connection.execute("DELETE FROM wes_biz.inbound_evidences WHERE id > 100")
            assert await connection.fetchval("SELECT cancelled_at FROM wes_biz.picking_tasks WHERE id=12") == now
        finally:
            await connection.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("starting_revision", [None, "3a17b9c40d62", "a72d4e6c9b13"])
async def test_release_merge_preserves_current_plugin_facts_from_both_baselines(starting_revision) -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from src.app.execution.models import InboundEvidence, InboundEvidenceKind

    merge_revision = "0f1be9a476d2"
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    assert scripts.get_heads() == [merge_revision]
    assert set(scripts.get_revision(merge_revision).down_revision) == {"3a17b9c40d62", "a72d4e6c9b13"}
    assert scripts.get_revision("a72d4e6c9b13").down_revision == "f6d0319c8a42"
    assert scripts.get_revision("f6d0319c8a42").down_revision == "e3fec1d9ffae"
    async with temporary_database() as (_database, database_url):
        tables = ["work_lines", "picking_tasks", "inbound_evidences"]
        before = {}
        if starting_revision:
            run_alembic("upgrade", starting_revision, database_url=database_url)
            engine = create_async_engine(database_url)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            try:
                async with sessions.begin() as db:
                    line = WorkLine(line_code="RELEASE-MERGE", line_name="发布合并验证", line_type="MANUAL")
                    evidence = InboundEvidence(
                        kind=InboundEvidenceKind.WMS_EVENT,
                        source_identity="RELEASE-MERGE-ISSUED",
                        operation="outbound.picking_task.issued@v1",
                        operation_id="019f33f0-58d7-7b4d-a23a-1b90aa5d4474",
                        payload_digest="b" * 64,
                        normalized_payload={"data": {}},
                        received_at=timezone.now_for_db(),
                    )
                    db.add_all([line, evidence])
                    await db.flush()
                    await db.execute(
                        sa.text(
                            "INSERT INTO wes_biz.picking_tasks "
                            "(id, task_id, task_type, status, queue_revision, dispatch_sequence, issued_at_ms, "
                            "issued_evidence_id, workline_id, created_at) "
                            "VALUES (1, 'RELEASE-MERGE', :type, 'EXECUTING', 1, 1, 1, :evidence, :line, :now)"
                        ),
                        {
                            "type": "AUTO" if starting_revision == "3a17b9c40d62" else "MANUAL",
                            "evidence": evidence.id,
                            "line": line.id,
                            "now": timezone.now_for_db(),
                        },
                    )
                    if starting_revision == "a72d4e6c9b13":
                        tables += ["manual_picking_source_arrivals", "manual_picking_departure_retries"]
                        await db.execute(
                            sa.text(
                                "INSERT INTO wes_biz.manual_picking_source_arrivals "
                                "(id, created_at, is_current, workline_id, picking_task_id, source_evidence_id, "
                                "source_causal_token, source_transport_task_id, plan_revision, rack_id, rack_face, "
                                "location_code, observed_at, audit_id, confirmation_digest) "
                                "VALUES (1, :now, true, :line, 1, :evidence, 7, 'ORIGINAL-CTU01', 1, "
                                "'RACK-1', 'A', 'SOURCE', :now, 42, :digest)"
                            ),
                            {
                                "line": line.id,
                                "evidence": evidence.id,
                                "now": timezone.now_for_db(),
                                "digest": "c" * 64,
                            },
                        )
                        await db.execute(
                            sa.text(
                                "INSERT INTO wes_biz.manual_picking_departure_retries "
                                "(id, created_at, workline_id, scope_identity, rack_id, step, retry_count, "
                                "last_transport_task_id, last_transport_causal_token, next_attempt_at) "
                                "VALUES (1, :now, :line, 'original-scope', 'RACK-1', 'CTU03', 2, "
                                "'ORIGINAL-CTU03', 8, :now)"
                            ),
                            {"line": line.id, "now": timezone.now_for_db()},
                        )
                    else:
                        tables += ["automatic_picking_task_completions"]
                        await db.execute(
                            sa.text(
                                "INSERT INTO wes_biz.automatic_picking_task_completions "
                                "(id, created_at, workline_id, picking_task_id, first_plan_due_at) "
                                "VALUES (1, :now, :line, 1, :now)"
                            ),
                            {"line": line.id, "now": timezone.now_for_db()},
                        )
            finally:
                await engine.dispose()

        async def snapshot():
            connection = await asyncpg.connect(database_url.replace("postgresql+asyncpg", "postgresql", 1))
            try:
                return {
                    table: [dict(row) for row in await connection.fetch(f'SELECT * FROM wes_biz."{table}" ORDER BY id')]
                    for table in tables
                }
            finally:
                await connection.close()

        if starting_revision:
            before = await snapshot()
        run_alembic("upgrade", "head", database_url=database_url)
        after = await snapshot()
        for table, rows in before.items():
            assert [{key: row[key] for key in old} for row, old in zip(after[table], rows, strict=True)] == rows
        connection = await asyncpg.connect(database_url.replace("postgresql+asyncpg", "postgresql", 1))
        try:
            await assert_database_head(connection, merge_revision)
            for table in (
                "automatic_picking_works",
                "automatic_picking_task_completions",
                "manual_picking_source_arrivals",
                "manual_picking_departure_retries",
            ):
                assert await connection.fetchval("SELECT to_regclass($1)", f"wes_biz.{table}")
        finally:
            await connection.close()
        run_alembic("upgrade", "head", database_url=database_url)
        assert await snapshot() == after
