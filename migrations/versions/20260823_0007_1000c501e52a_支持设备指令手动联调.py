"""支持设备指令手动联调

Revision ID: 1000c501e52a
Revises: db0859fd3259
Create Date: 2026-08-23 00:07:40.590691+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1000c501e52a"
down_revision: Union[str, Sequence[str], None] = "db0859fd3259"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Allow complete MANUAL_DEBUG commands to freeze their own ECS context."""

    op.add_column(
        "device_commands",
        sa.Column("endpoint_base_url", sa.String(length=255), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "device_commands",
        sa.Column("command_timeout_ms", sa.Integer(), nullable=True),
        schema="wes_biz",
    )
    op.alter_column(
        "device_commands",
        "line_run_epoch_id",
        existing_type=sa.Integer(),
        nullable=True,
        schema="wes_biz",
    )
    op.alter_column(
        "device_commands",
        "device_binding_id",
        existing_type=sa.Integer(),
        nullable=True,
        schema="wes_biz",
    )
    op.create_check_constraint(
        "device_command_execution_context_complete",
        "device_commands",
        "((execution_ref_type = 'MANUAL_DEBUG' AND line_run_epoch_id IS NULL "
        "AND device_binding_id IS NULL AND endpoint_base_url IS NOT NULL "
        "AND command_timeout_ms IS NOT NULL) OR "
        "(execution_ref_type <> 'MANUAL_DEBUG' AND line_run_epoch_id IS NOT NULL "
        "AND device_binding_id IS NOT NULL AND endpoint_base_url IS NULL "
        "AND command_timeout_ms IS NULL))",
        schema="wes_biz",
    )
    op.create_check_constraint(
        "device_command_endpoint_nonempty",
        "device_commands",
        "endpoint_base_url IS NULL OR length(endpoint_base_url) > 0",
        schema="wes_biz",
    )
    op.create_check_constraint(
        "device_command_timeout_positive",
        "device_commands",
        "command_timeout_ms IS NULL OR command_timeout_ms > 0",
        schema="wes_biz",
    )
    op.create_index(
        "ux_device_commands_manual_debug_identity",
        "device_commands",
        ["execution_ref_id"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("execution_ref_type = 'MANUAL_DEBUG'"),
    )


def downgrade() -> None:
    """Remove MANUAL_DEBUG execution context support."""

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM wes_biz.device_commands
                WHERE execution_ref_type = 'MANUAL_DEBUG'
            ) THEN
                RAISE EXCEPTION 'cannot downgrade while MANUAL_DEBUG DeviceCommand evidence exists';
            END IF;
        END
        $$
        """
    )
    op.drop_index("ux_device_commands_manual_debug_identity", table_name="device_commands", schema="wes_biz")
    for constraint in (
        "device_command_timeout_positive",
        "device_command_endpoint_nonempty",
        "device_command_execution_context_complete",
    ):
        op.drop_constraint(constraint, "device_commands", schema="wes_biz", type_="check")
    op.alter_column(
        "device_commands",
        "device_binding_id",
        existing_type=sa.Integer(),
        nullable=False,
        schema="wes_biz",
    )
    op.alter_column(
        "device_commands",
        "line_run_epoch_id",
        existing_type=sa.Integer(),
        nullable=False,
        schema="wes_biz",
    )
    op.drop_column("device_commands", "command_timeout_ms", schema="wes_biz")
    op.drop_column("device_commands", "endpoint_base_url", schema="wes_biz")
