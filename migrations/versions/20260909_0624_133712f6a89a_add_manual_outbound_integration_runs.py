"""add manual outbound integration runs

Revision ID: 133712f6a89a
Revises: d11f8c6fdb0d
Create Date: 2026-09-09 06:24:18.742540+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "133712f6a89a"
down_revision: Union[str, Sequence[str], None] = "d11f8c6fdb0d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "workline_integration_runs",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="主键 ID"),
        sa.Column("version", sa.Integer(), server_default="0", nullable=False, comment="版本号"),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人ID"),
        sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人ID"),
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间 (UTC)"),
        sa.Column("updated_at", sa.DateTime(), nullable=True, comment="更新时间 (UTC)"),
        sa.Column("run_id", sa.String(length=80), nullable=False),
        sa.Column("workline_id", sa.BigInteger(), nullable=False),
        sa.Column("workline_code", sa.String(length=50), nullable=False),
        sa.Column("scenario_key", sa.String(length=80), nullable=False),
        sa.Column("expected_plugin_key", sa.String(length=100), nullable=False),
        sa.Column("profile", sa.String(length=30), nullable=False),
        sa.Column("environment_label", sa.String(length=80), nullable=False),
        sa.Column("operator_user_id", sa.BigInteger(), nullable=False),
        sa.Column("active_scope", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("current_phase", sa.String(length=30), nullable=False),
        sa.Column("picking_task_id", sa.BigInteger(), nullable=True),
        sa.Column("task_id", sa.String(length=100), nullable=True),
        sa.Column("issued_operation_id", sa.String(length=36), nullable=True),
        sa.Column("bin_code", sa.String(length=100), nullable=True),
        sa.Column("device_code", sa.String(length=100), nullable=True),
        sa.Column("rack_id", sa.String(length=100), nullable=True),
        sa.Column("configuration_json", sa.JSON(), nullable=False),
        sa.Column("attention_code", sa.String(length=120), nullable=True),
        sa.Column("attention_detail", sa.Text(), nullable=True),
        sa.Column("wms_cleanup_confirmed", sa.Boolean(), nullable=False),
        sa.Column("site_cleanup_confirmed", sa.Boolean(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('CREATED','WAITING_TASK','ACTIVE','WAITING_EXTERNAL','COMPLETED',"
            "'NEEDS_ATTENTION','CLOSED_BY_OPERATOR')",
            name=op.f("ck_workline_integration_runs_workline_integration_run_status_valid"),
        ),
        sa.CheckConstraint(
            "profile IN ('CONTRACT_SIMULATION','DEVICE_INTEGRATION','FULL_SITE_INTEGRATION')",
            name=op.f("ck_workline_integration_runs_workline_integration_run_profile_valid"),
        ),
        sa.CheckConstraint(
            "scenario_key = 'manual_outbound_picking@v1'",
            name=op.f("ck_workline_integration_runs_workline_integration_run_scenario_valid"),
        ),
        sa.CheckConstraint(
            "current_phase IN ('BIND_TASK','TASK_PREPARE','PLAN_RECEIPT','RACK_TRANSPORT','RACK_ARRIVAL',"
            "'BIN_INBOUND_BATCH','BIN_TRANSPORT','POINT1_ARRIVAL','POINT2_SCAN','WORK_ADMISSION',"
            "'WORK_COMPLETION','POINT2_RELEASE','POINT3_ROUTE','RETURN_BUFFER','BIN_RETURN_BATCH',"
            "'BIN_RETURN_TRANSPORT','RACK_DEPARTURE','TASK_COMPLETION','COMPLETION_REPORT','CLEANUP')",
            name=op.f("ck_workline_integration_runs_workline_integration_run_phase_valid"),
        ),
        sa.CheckConstraint(
            "(status = 'CLOSED_BY_OPERATOR' AND active_scope IS NULL) OR "
            "(status <> 'CLOSED_BY_OPERATOR' AND active_scope IS NOT NULL)",
            name=op.f("ck_workline_integration_runs_workline_integration_run_active_scope_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["picking_task_id"],
            ["wes_biz.picking_tasks.id"],
            name=op.f("fk_workline_integration_runs_picking_task_id_picking_tasks"),
        ),
        sa.ForeignKeyConstraint(
            ["workline_id"],
            ["wes_biz.work_lines.id"],
            name=op.f("fk_workline_integration_runs_workline_id_work_lines"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workline_integration_runs")),
        sa.UniqueConstraint("active_scope", name="ux_workline_integration_runs_active_scope"),
        sa.UniqueConstraint("run_id", name="ux_workline_integration_runs_run_id"),
        schema="wes_runtime",
    )
    op.create_index(
        "ix_workline_integration_runs_recent",
        "workline_integration_runs",
        ["updated_at", "id"],
        unique=False,
        schema="wes_runtime",
    )
    op.create_table(
        "workline_integration_run_steps",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="主键 ID"),
        sa.Column("version", sa.Integer(), server_default="0", nullable=False, comment="版本号"),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人ID"),
        sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人ID"),
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间 (UTC)"),
        sa.Column("updated_at", sa.DateTime(), nullable=True, comment="更新时间 (UTC)"),
        sa.Column("run_id", sa.String(length=80), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("client_request_id", sa.String(length=120), nullable=True),
        sa.Column("operation", sa.String(length=160), nullable=True),
        sa.Column("operation_id", sa.String(length=160), nullable=True),
        sa.Column("wms_confirmation_id", sa.BigInteger(), nullable=True),
        sa.Column("transport_task_id", sa.String(length=80), nullable=True),
        sa.Column("device_command_code", sa.String(length=80), nullable=True),
        sa.Column("request_summary_json", sa.JSON(), nullable=False),
        sa.Column("result_summary_json", sa.JSON(), nullable=False),
        sa.Column("reason_code", sa.String(length=120), nullable=True),
        sa.CheckConstraint(
            "ordinal >= 0",
            name=op.f("ck_workline_integration_run_steps_workline_integration_step_ordinal_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','WAITING','SUCCEEDED','NEEDS_ATTENTION')",
            name=op.f("ck_workline_integration_run_steps_workline_integration_step_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["wes_runtime.workline_integration_runs.run_id"],
            name=op.f("fk_workline_integration_run_steps_run_id_workline_integration_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["wms_confirmation_id"],
            ["wes_biz.wms_confirmations.id"],
            name=op.f("fk_workline_integration_run_steps_wms_confirmation_id_wms_confirmations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workline_integration_run_steps")),
        sa.UniqueConstraint("client_request_id", name="ux_workline_integration_steps_client_request"),
        sa.UniqueConstraint("run_id", "ordinal", name="ux_workline_integration_steps_run_ordinal"),
        schema="wes_runtime",
    )
    for name, column in (
        ("ix_workline_integration_steps_confirmation", "wms_confirmation_id"),
        ("ix_workline_integration_steps_transport", "transport_task_id"),
        ("ix_workline_integration_steps_device", "device_command_code"),
    ):
        op.create_index(
            name,
            "workline_integration_run_steps",
            [column],
            unique=False,
            schema="wes_runtime",
        )


def downgrade() -> None:
    """Downgrade schema."""
    for name in (
        "ix_workline_integration_steps_device",
        "ix_workline_integration_steps_transport",
        "ix_workline_integration_steps_confirmation",
    ):
        op.drop_index(name, table_name="workline_integration_run_steps", schema="wes_runtime")
    op.drop_table("workline_integration_run_steps", schema="wes_runtime")
    op.drop_index(
        "ix_workline_integration_runs_recent",
        table_name="workline_integration_runs",
        schema="wes_runtime",
    )
    op.drop_table("workline_integration_runs", schema="wes_runtime")
