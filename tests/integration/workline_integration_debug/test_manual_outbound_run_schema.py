from __future__ import annotations

import pytest
from sqlalchemy import text

from src.app.workline_integration_debug.models import IntegrationRun, IntegrationRunStep
from tests.support.postgresql_heavy import migrated_database

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
