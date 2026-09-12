from __future__ import annotations

import subprocess

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.app.execution.models import WmsConfirmation
from src.app.workline.models import WorkLine
from src.app.workline_integration_debug.models import IntegrationRun, IntegrationRunStep
from src.utils.timezone import timezone
from tests.support.postgresql_catalog import assert_database_head
from tests.support.postgresql_heavy import connect, migrated_database, run_alembic, temporary_database

pytestmark = pytest.mark.asyncio


async def test_manual_outbound_run_model_declares_runtime_fences() -> None:
    assert IntegrationRun.__table__.schema == "wes_runtime"
    assert IntegrationRunStep.__table__.schema == "wes_runtime"
    run_constraints = {constraint.name for constraint in IntegrationRun.__table__.constraints}
    step_constraints = {constraint.name for constraint in IntegrationRunStep.__table__.constraints}
    assert {
        "ux_workline_integration_runs_run_id",
        "ux_workline_integration_runs_active_scope",
        "ck_workline_integration_runs_workline_integration_run_active_scope_consistent",
    } <= run_constraints
    assert {
        "ux_workline_integration_steps_run_ordinal",
        "ux_workline_integration_steps_client_request",
    } <= step_constraints


async def test_manual_outbound_run_migration_creates_identity_and_link_indexes() -> None:
    async with migrated_database() as (_url, sessions):
        async with sessions() as db:
            rows = await db.execute(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname = 'wes_runtime' "
                    "AND tablename IN ('workline_integration_runs', 'workline_integration_run_steps')"
                )
            )
            indexes = {row[0] for row in rows}

    assert {
        "ux_workline_integration_runs_run_id",
        "ux_workline_integration_runs_active_scope",
        "ux_workline_integration_steps_client_request",
        "ix_workline_integration_steps_confirmation",
        "ix_workline_integration_steps_transport",
        "ix_workline_integration_steps_device",
    } <= indexes


@pytest.mark.parametrize("blocker", ["run", "confirmation"])
async def test_completion_report_retirement_preserves_unresolved_state(blocker: str) -> None:
    base_revision = "f7cf0cd8c6d4"
    target_revision = "b7da7ecdc74b"
    async with temporary_database() as (database, url):
        run_alembic("upgrade", base_revision, database_url=url)
        engine = create_async_engine(url)
        try:
            async with async_sessionmaker(engine).begin() as db:
                db.add(WorkLine(id=42, line_code="RETIRE", line_name="Retirement guard", line_type="AUTO"))
                await db.flush()
                if blocker == "run":
                    db.add(
                        IntegrationRun(
                            id=43,
                            run_id="retirement-guard",
                            workline_id=42,
                            workline_code="RETIRE",
                            scenario_key="manual_outbound_picking@v1",
                            expected_plugin_key="manual_bin_processing",
                            profile="CONTRACT_SIMULATION",
                            environment_label="test",
                            operator_user_id=1,
                            active_scope="WORKLINE:42",
                            status="WAITING_EXTERNAL",
                            current_phase="COMPLETION_REPORT",
                        )
                    )
                else:
                    db.add(
                        WmsConfirmation(
                            id=43,
                            operation="outbound.manual_bin.completion_apply_report@v1",
                            operation_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
                            workline_id=42,
                            request_digest="a" * 64,
                            request_payload={"preserved": True},
                            deadline_at=timezone.now_for_db(),
                        )
                    )
        finally:
            await engine.dispose()

        row_sql = (
            "SELECT row_to_json(r)::text FROM wes_runtime.workline_integration_runs r WHERE id=43"
            if blocker == "run"
            else "SELECT row_to_json(r)::text FROM wes_biz.wms_confirmations r WHERE id=43"
        )
        constraint_sql = (
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid='wes_runtime.workline_integration_runs'::regclass "
            "AND contype='c' AND pg_get_constraintdef(oid) LIKE '%current_phase%'"
        )
        connection = await connect(database)
        try:
            original_row = await connection.fetchval(row_sql)
            original_constraint = await connection.fetchval(constraint_sql)
            assert original_row is not None
            assert "COMPLETION_REPORT" in original_constraint
        finally:
            await connection.close()

        with pytest.raises(subprocess.CalledProcessError) as failure:
            run_alembic("upgrade", target_revision, database_url=url)
        expected_error = (
            "Resolve COMPLETION_REPORT runs"
            if blocker == "run"
            else "Resolve outstanding completion_apply_report obligations"
        )
        assert expected_error in failure.value.stderr

        connection = await connect(database)
        try:
            await assert_database_head(connection, base_revision)
            assert await connection.fetchval(row_sql) == original_row
            assert await connection.fetchval(constraint_sql) == original_constraint
            # 仅解决隔离测试 fixture 的阻塞，验证迁移不会自行完成历史业务对象。
            if blocker == "run":
                await connection.execute(
                    "UPDATE wes_runtime.workline_integration_runs SET current_phase='POINT3_ROUTE' WHERE id=43"
                )
            else:
                await connection.execute("UPDATE wes_biz.wms_confirmations SET status='COMPLETED' WHERE id=43")
            resolved_row = await connection.fetchval(row_sql)
        finally:
            await connection.close()

        run_alembic("upgrade", target_revision, database_url=url)
        connection = await connect(database)
        try:
            await assert_database_head(connection, target_revision)
            assert await connection.fetchval(row_sql) == resolved_row
            assert "COMPLETION_REPORT" not in await connection.fetchval(constraint_sql)
        finally:
            await connection.close()
