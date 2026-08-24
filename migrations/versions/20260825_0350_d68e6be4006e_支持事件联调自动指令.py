"""支持事件联调自动指令

Revision ID: d68e6be4006e
Revises: f11b613771fa
Create Date: 2026-08-25 03:50:24.812536+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d68e6be4006e"
down_revision: Union[str, Sequence[str], None] = "f11b613771fa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    op.drop_constraint(
        "device_command_execution_context_complete",
        "device_commands",
        schema="wes_biz",
        type_="check",
    )
    op.create_check_constraint(
        "device_command_execution_context_complete",
        "device_commands",
        "((execution_ref_type IN ('MANUAL_DEBUG', 'EVENT_DEBUG') AND line_run_epoch_id IS NULL "
        "AND device_binding_id IS NULL AND material_execution_id IS NULL "
        "AND endpoint_base_url IS NOT NULL AND command_timeout_ms IS NOT NULL) OR "
        "(execution_ref_type NOT IN ('MANUAL_DEBUG', 'EVENT_DEBUG') AND line_run_epoch_id IS NOT NULL "
        "AND device_binding_id IS NOT NULL AND endpoint_base_url IS NULL AND command_timeout_ms IS NULL))",
        schema="wes_biz",
    )
    op.drop_constraint(
        "device_command_manual_debug_audit_complete",
        "device_commands",
        schema="wes_biz",
        type_="check",
    )
    op.create_check_constraint(
        "device_command_manual_debug_audit_complete",
        "device_commands",
        "((execution_ref_type = 'MANUAL_DEBUG' AND execution_reason IS NOT NULL "
        "AND length(trim(execution_reason)) > 0 AND created_by IS NOT NULL) OR "
        "(execution_ref_type = 'EVENT_DEBUG' AND execution_reason IS NOT NULL "
        "AND length(trim(execution_reason)) > 0 AND created_by IS NULL) OR "
        "(execution_ref_type NOT IN ('MANUAL_DEBUG', 'EVENT_DEBUG') AND execution_reason IS NULL))",
        schema="wes_biz",
    )
    op.create_index(
        "ux_device_commands_event_debug_identity",
        "device_commands",
        ["execution_ref_id"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("execution_ref_type = 'EVENT_DEBUG'"),
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.execute("DELETE FROM wes_biz.device_commands WHERE execution_ref_type = 'EVENT_DEBUG'")
    op.drop_index("ux_device_commands_event_debug_identity", table_name="device_commands", schema="wes_biz")
    op.drop_constraint(
        "device_command_manual_debug_audit_complete",
        "device_commands",
        schema="wes_biz",
        type_="check",
    )
    op.create_check_constraint(
        "device_command_manual_debug_audit_complete",
        "device_commands",
        "((execution_ref_type = 'MANUAL_DEBUG' AND execution_reason IS NOT NULL "
        "AND length(trim(execution_reason)) > 0 AND created_by IS NOT NULL) OR "
        "(execution_ref_type <> 'MANUAL_DEBUG' AND execution_reason IS NULL))",
        schema="wes_biz",
    )
    op.drop_constraint(
        "device_command_execution_context_complete",
        "device_commands",
        schema="wes_biz",
        type_="check",
    )
    op.create_check_constraint(
        "device_command_execution_context_complete",
        "device_commands",
        "((execution_ref_type = 'MANUAL_DEBUG' AND line_run_epoch_id IS NULL "
        "AND device_binding_id IS NULL AND material_execution_id IS NULL "
        "AND endpoint_base_url IS NOT NULL AND command_timeout_ms IS NOT NULL) OR "
        "(execution_ref_type <> 'MANUAL_DEBUG' AND line_run_epoch_id IS NOT NULL "
        "AND device_binding_id IS NOT NULL AND endpoint_base_url IS NULL AND command_timeout_ms IS NULL))",
        schema="wes_biz",
    )
