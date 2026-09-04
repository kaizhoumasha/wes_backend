"""扩展 PickingTask prepare 领取合同

Revision ID: ff5d0af61f91
Revises: a0f4b56d0f50
Create Date: 2026-09-04 14:37:46.825292+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ff5d0af61f91"
down_revision: Union[str, Sequence[str], None] = "a0f4b56d0f50"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """增加 prepare 任务绑定，并把 WmsConfirmation 收敛为三类显式 owner。"""
    op.add_column("picking_tasks", sa.Column("workline_id", sa.Integer(), nullable=True), schema="wes_biz")
    op.add_column("picking_tasks", sa.Column("line_run_epoch_id", sa.Integer(), nullable=True), schema="wes_biz")
    op.create_foreign_key(
        op.f("fk_picking_tasks_workline_id_work_lines"),
        "picking_tasks",
        "work_lines",
        ["workline_id"],
        ["id"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )
    op.create_foreign_key(
        op.f("fk_picking_tasks_line_run_epoch_id_line_run_epochs"),
        "picking_tasks",
        "line_run_epochs",
        ["line_run_epoch_id"],
        ["id"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )
    op.create_check_constraint(
        op.f("ck_picking_tasks_picking_task_binding_matches_status"),
        "picking_tasks",
        "(status = 'QUEUED' AND workline_id IS NULL AND line_run_epoch_id IS NULL) OR "
        "(status IN ('PREPARING', 'EXECUTING', 'EXECUTION_COMPLETED') "
        "AND workline_id IS NOT NULL AND line_run_epoch_id IS NOT NULL)",
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_picking_tasks_workline_id"),
        "picking_tasks",
        ["workline_id"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_picking_tasks_line_run_epoch_id"),
        "picking_tasks",
        ["line_run_epoch_id"],
        unique=False,
        schema="wes_biz",
    )
    op.drop_index("ix_picking_tasks_queue", table_name="picking_tasks", schema="wes_biz")
    op.create_index(
        "ix_picking_tasks_queue",
        "picking_tasks",
        ["task_type", "dispatch_sequence", "id"],
        unique=False,
        schema="wes_biz",
        postgresql_where=sa.text("status = 'QUEUED'"),
    )
    op.create_index(
        "ux_picking_tasks_active_workline",
        "picking_tasks",
        ["workline_id"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("status IN ('PREPARING', 'EXECUTING')"),
    )

    op.alter_column("wms_confirmations", "material_execution_id", nullable=True, schema="wes_biz")
    op.add_column("wms_confirmations", sa.Column("bin_execution_id", sa.BigInteger(), nullable=True), schema="wes_biz")
    op.add_column("wms_confirmations", sa.Column("picking_task_id", sa.BigInteger(), nullable=True), schema="wes_biz")
    op.create_foreign_key(
        op.f("fk_wms_confirmations_bin_execution_id_bin_executions"),
        "wms_confirmations",
        "bin_executions",
        ["bin_execution_id"],
        ["id"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )
    op.create_foreign_key(
        op.f("fk_wms_confirmations_picking_task_id_picking_tasks"),
        "wms_confirmations",
        "picking_tasks",
        ["picking_task_id"],
        ["id"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )
    op.create_check_constraint(
        op.f("ck_wms_confirmations_wms_confirmation_exactly_one_owner"),
        "wms_confirmations",
        "(CASE WHEN material_execution_id IS NOT NULL THEN 1 ELSE 0 END + "
        "CASE WHEN bin_execution_id IS NOT NULL THEN 1 ELSE 0 END + "
        "CASE WHEN picking_task_id IS NOT NULL THEN 1 ELSE 0 END) = 1",
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_wms_confirmations_bin_execution_id"),
        "wms_confirmations",
        ["bin_execution_id"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_wms_confirmations_picking_task_id"),
        "wms_confirmations",
        ["picking_task_id"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        "ux_wms_confirmations_picking_task_operation",
        "wms_confirmations",
        ["picking_task_id", "operation"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("picking_task_id IS NOT NULL"),
    )


def downgrade() -> None:
    """当前未发布业务不提供回退迁移。"""
    raise NotImplementedError("PickingTask prepare migration does not support downgrade")
