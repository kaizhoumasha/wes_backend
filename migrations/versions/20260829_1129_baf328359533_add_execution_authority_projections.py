"""add execution authority projections

Revision ID: baf328359533
Revises: 7bdca6f754ee
Create Date: 2026-08-29 11:29:40.675843+08:00

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "baf328359533"
down_revision: Union[str, Sequence[str], None] = "7bdca6f754ee"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """在停写且无活动执行时直接切换到唯一 execution projection schema。"""

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM wes_biz.work_lines WHERE is_active IS TRUE LIMIT 1)
               OR EXISTS (SELECT 1 FROM wes_biz.line_run_epochs WHERE status = 'ACTIVE' LIMIT 1)
               OR EXISTS (
                    SELECT 1 FROM wes_runtime.transport_tasks
                    WHERE status IN ('PENDING', 'ACCEPTED', 'RECONCILING') LIMIT 1
               )
               OR EXISTS (
                    SELECT 1 FROM wes_biz.device_commands
                    WHERE status IN ('PENDING', 'DISPATCHING', 'ACKNOWLEDGED', 'RECONCILING') LIMIT 1
               )
               OR EXISTS (
                    SELECT 1 FROM wes_biz.wms_confirmations
                    WHERE status <> 'COMPLETED' LIMIT 1
               ) THEN
                RAISE EXCEPTION
                    'execution projection direct cutover requires inactive WorkLines and no unclosed work';
            END IF;
        END
        $$
        """
    )

    op.drop_table("transport_position_projections", schema="wes_runtime")

    op.create_table(
        "bin_executions",
        *_enterprise_columns(),
        sa.Column("execution_code", sa.String(length=120), nullable=False),
        sa.Column("bin_id", sa.String(length=100), nullable=False),
        sa.Column("workline_id", sa.Integer(), nullable=False),
        sa.Column("line_run_epoch_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("status IN ('ACTIVE', 'CLOSED')", name="bin_execution_status_valid"),
        sa.ForeignKeyConstraint(["workline_id"], ["wes_biz.work_lines.id"]),
        sa.ForeignKeyConstraint(["line_run_epoch_id"], ["wes_biz.line_run_epochs.id"]),
        sa.UniqueConstraint("execution_code", name="ux_bin_executions_execution_code"),
        schema="wes_biz",
    )
    op.create_index(
        "ux_bin_executions_active_bin",
        "bin_executions",
        ["bin_id"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_index(
        "ix_bin_executions_epoch_status",
        "bin_executions",
        ["line_run_epoch_id", "status", "id"],
        schema="wes_biz",
    )
    for column in ("bin_id", "workline_id", "line_run_epoch_id", "status"):
        op.create_index(
            op.f(f"ix_wes_biz_bin_executions_{column}"),
            "bin_executions",
            [column],
            schema="wes_biz",
        )

    op.create_table(
        "position_projections",
        *_enterprise_columns(),
        sa.Column("object_type", sa.String(length=10), nullable=False),
        sa.Column("object_id", sa.String(length=100), nullable=False),
        sa.Column("workline_id", sa.Integer(), nullable=False),
        sa.Column("line_run_epoch_id", sa.Integer(), nullable=False),
        sa.Column("bin_execution_id", sa.BigInteger(), nullable=True),
        sa.Column("position_json", sa.JSON(), nullable=True),
        sa.Column("position_unknown", sa.Boolean(), nullable=False),
        sa.Column("arrival_face", sa.String(length=1), nullable=True),
        sa.Column("source_operation_id", sa.String(length=36), nullable=False),
        sa.Column("source_transport_task_id", sa.String(length=80), nullable=False),
        sa.CheckConstraint("object_type IN ('RACK', 'BIN')", name="position_projection_object_type_valid"),
        sa.CheckConstraint(
            "(object_type = 'RACK' AND bin_execution_id IS NULL) OR "
            "(object_type = 'BIN' AND bin_execution_id IS NOT NULL)",
            name="position_projection_bin_authority_valid",
        ),
        sa.ForeignKeyConstraint(["workline_id"], ["wes_biz.work_lines.id"]),
        sa.ForeignKeyConstraint(["line_run_epoch_id"], ["wes_biz.line_run_epochs.id"]),
        sa.ForeignKeyConstraint(["bin_execution_id"], ["wes_biz.bin_executions.id"]),
        sa.UniqueConstraint("object_type", "object_id", name="ux_position_projection_object"),
        schema="wes_biz",
    )
    for column in ("workline_id", "line_run_epoch_id", "bin_execution_id"):
        op.create_index(
            op.f(f"ix_wes_biz_position_projections_{column}"),
            "position_projections",
            [column],
            schema="wes_biz",
        )
    op.create_index(
        "ix_position_projection_epoch",
        "position_projections",
        ["line_run_epoch_id", "id"],
        schema="wes_biz",
    )
    op.create_index(
        "ix_position_projection_source_task",
        "position_projections",
        ["source_transport_task_id"],
        schema="wes_biz",
    )

    for column in (
        sa.Column("authority_workline_id", sa.Integer(), nullable=True),
        sa.Column("authority_line_run_epoch_id", sa.Integer(), nullable=True),
        sa.Column("authority_bin_execution_id", sa.BigInteger(), nullable=True),
    ):
        op.add_column("transport_tasks", column, schema="wes_runtime")
    op.create_foreign_key(
        "fk_transport_tasks_authority_workline_id_work_lines",
        "transport_tasks",
        "work_lines",
        ["authority_workline_id"],
        ["id"],
        source_schema="wes_runtime",
        referent_schema="wes_biz",
    )
    op.create_foreign_key(
        "fk_transport_tasks_authority_line_run_epoch_id_line_run_epochs",
        "transport_tasks",
        "line_run_epochs",
        ["authority_line_run_epoch_id"],
        ["id"],
        source_schema="wes_runtime",
        referent_schema="wes_biz",
    )
    op.create_foreign_key(
        "fk_transport_tasks_authority_bin_execution_id_bin_executions",
        "transport_tasks",
        "bin_executions",
        ["authority_bin_execution_id"],
        ["id"],
        source_schema="wes_runtime",
        referent_schema="wes_biz",
    )
    op.create_check_constraint(
        "transport_execution_authority_all_or_none",
        "transport_tasks",
        "(authority_workline_id IS NULL AND authority_line_run_epoch_id IS NULL "
        "AND authority_bin_execution_id IS NULL) OR "
        "(authority_workline_id IS NOT NULL AND authority_line_run_epoch_id IS NOT NULL)",
        schema="wes_runtime",
    )


def downgrade() -> None:
    """该 target-only direct cutover 不提供旧 projection schema 回退。"""

    raise RuntimeError("execution authority projection migration is irreversible")


def _enterprise_columns() -> tuple[sa.Column[object], ...]:
    return (
        sa.Column("version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
