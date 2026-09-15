"""增加作业线清线归档状态

Revision ID: 2b1adb268fdc
Revises: 0428e7dff7da
Create Date: 2026-09-15 14:40:47.178188+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2b1adb268fdc"
down_revision: Union[str, Sequence[str], None] = "0428e7dff7da"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("picking_tasks", sa.Column("archived_at", sa.DateTime(), nullable=True), schema="wes_biz")
    op.drop_constraint(
        op.f("ck_picking_tasks_picking_task_status_valid"),
        "picking_tasks",
        schema="wes_biz",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_picking_tasks_picking_task_binding_matches_status"),
        "picking_tasks",
        schema="wes_biz",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_picking_tasks_picking_task_status_valid"),
        "picking_tasks",
        "status IN ('QUEUED', 'PREPARING', 'EXECUTING', 'EXECUTION_COMPLETED', 'ARCHIVED')",
        schema="wes_biz",
    )

    op.drop_constraint(
        op.f("ck_workline_integration_runs_workline_integration_run_status_valid"),
        "workline_integration_runs",
        schema="wes_runtime",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_workline_integration_runs_workline_integration_run_active_scope_consistent"),
        "workline_integration_runs",
        schema="wes_runtime",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_workline_integration_runs_workline_integration_run_status_valid"),
        "workline_integration_runs",
        "status IN ('CREATED','WAITING_TASK','ACTIVE','WAITING_EXTERNAL','COMPLETED',"
        "'NEEDS_ATTENTION','CLOSED_BY_OPERATOR','ARCHIVED')",
        schema="wes_runtime",
    )
    op.create_check_constraint(
        op.f("ck_workline_integration_runs_workline_integration_run_active_scope_consistent"),
        "workline_integration_runs",
        "(status IN ('CLOSED_BY_OPERATOR','ARCHIVED') AND active_scope IS NULL) OR "
        "(status NOT IN ('CLOSED_BY_OPERATOR','ARCHIVED') AND active_scope IS NOT NULL)",
        schema="wes_runtime",
    )
    op.create_check_constraint(
        op.f("ck_picking_tasks_picking_task_binding_matches_status"),
        "picking_tasks",
        "(status = 'QUEUED' AND workline_id IS NULL) OR "
        "(status IN ('PREPARING', 'EXECUTING', 'EXECUTION_COMPLETED', 'ARCHIVED') AND workline_id IS NOT NULL)",
        schema="wes_biz",
    )
    op.create_check_constraint(
        op.f("ck_picking_tasks_picking_task_archive_consistent"),
        "picking_tasks",
        "(status = 'ARCHIVED') = (archived_at IS NOT NULL)",
        schema="wes_biz",
    )

    op.add_column(
        "manual_picking_passages",
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        schema="wes_biz",
    )
    op.create_check_constraint(
        op.f("ck_manual_picking_passages_manual_picking_archive_closed"),
        "manual_picking_passages",
        "archived_at IS NULL OR disposition = 'CLOSED'",
        schema="wes_biz",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM wes_biz.picking_tasks WHERE status = 'ARCHIVED') "
        "THEN RAISE EXCEPTION 'cannot downgrade while archived picking tasks exist'; END IF; END $$"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM wes_runtime.workline_integration_runs WHERE status = 'ARCHIVED') "
        "THEN RAISE EXCEPTION 'cannot downgrade while archived integration runs exist'; END IF; END $$"
    )
    op.drop_constraint(
        op.f("ck_manual_picking_passages_manual_picking_archive_closed"),
        "manual_picking_passages",
        schema="wes_biz",
        type_="check",
    )
    op.drop_column("manual_picking_passages", "archived_at", schema="wes_biz")

    op.drop_constraint(
        op.f("ck_workline_integration_runs_workline_integration_run_active_scope_consistent"),
        "workline_integration_runs",
        schema="wes_runtime",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_workline_integration_runs_workline_integration_run_status_valid"),
        "workline_integration_runs",
        schema="wes_runtime",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_workline_integration_runs_workline_integration_run_status_valid"),
        "workline_integration_runs",
        "status IN ('CREATED','WAITING_TASK','ACTIVE','WAITING_EXTERNAL','COMPLETED',"
        "'NEEDS_ATTENTION','CLOSED_BY_OPERATOR')",
        schema="wes_runtime",
    )
    op.create_check_constraint(
        op.f("ck_workline_integration_runs_workline_integration_run_active_scope_consistent"),
        "workline_integration_runs",
        "(status = 'CLOSED_BY_OPERATOR' AND active_scope IS NULL) OR "
        "(status <> 'CLOSED_BY_OPERATOR' AND active_scope IS NOT NULL)",
        schema="wes_runtime",
    )

    op.drop_constraint(
        op.f("ck_picking_tasks_picking_task_archive_consistent"),
        "picking_tasks",
        schema="wes_biz",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_picking_tasks_picking_task_binding_matches_status"),
        "picking_tasks",
        schema="wes_biz",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_picking_tasks_picking_task_status_valid"),
        "picking_tasks",
        schema="wes_biz",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_picking_tasks_picking_task_status_valid"),
        "picking_tasks",
        "status IN ('QUEUED', 'PREPARING', 'EXECUTING', 'EXECUTION_COMPLETED')",
        schema="wes_biz",
    )
    op.create_check_constraint(
        op.f("ck_picking_tasks_picking_task_binding_matches_status"),
        "picking_tasks",
        "(status = 'QUEUED' AND workline_id IS NULL) OR "
        "(status IN ('PREPARING', 'EXECUTING', 'EXECUTION_COMPLETED') AND workline_id IS NOT NULL)",
        schema="wes_biz",
    )
    op.drop_column("picking_tasks", "archived_at", schema="wes_biz")
