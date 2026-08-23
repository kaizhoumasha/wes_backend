"""audit manual device command

Revision ID: 11013119b97d
Revises: 1000c501e52a
Create Date: 2026-08-23 16:16:15.080729+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "11013119b97d"
down_revision: Union[str, Sequence[str], None] = "1000c501e52a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _clear_device_command_ledger() -> None:
    """Drop test-only commands without deleting the business rows that referenced them."""

    op.execute("UPDATE wes_biz.runtime_holds SET source_command_id = NULL WHERE source_command_id IS NOT NULL")
    op.execute("UPDATE wes_biz.ng_return_items SET source_command_id = NULL WHERE source_command_id IS NOT NULL")
    op.execute("UPDATE wes_biz.workline_timelines SET related_command_id = NULL WHERE related_command_id IS NOT NULL")
    op.execute("DELETE FROM wes_biz.device_commands")


def upgrade() -> None:
    """Upgrade schema."""

    _clear_device_command_ledger()
    op.add_column(
        "device_commands",
        sa.Column("execution_reason", sa.String(length=500), nullable=True),
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
        "AND endpoint_base_url IS NOT NULL "
        "AND command_timeout_ms IS NOT NULL) OR "
        "(execution_ref_type <> 'MANUAL_DEBUG' AND line_run_epoch_id IS NOT NULL "
        "AND device_binding_id IS NOT NULL AND endpoint_base_url IS NULL "
        "AND command_timeout_ms IS NULL))",
        schema="wes_biz",
    )
    op.create_check_constraint(
        "device_command_manual_debug_audit_complete",
        "device_commands",
        "((execution_ref_type = 'MANUAL_DEBUG' AND execution_reason IS NOT NULL "
        "AND length(trim(execution_reason)) > 0 "
        "AND created_by IS NOT NULL) OR "
        "(execution_ref_type <> 'MANUAL_DEBUG' AND execution_reason IS NULL))",
        schema="wes_biz",
    )


def downgrade() -> None:
    """Downgrade schema."""

    _clear_device_command_ledger()
    op.drop_constraint(
        "device_command_manual_debug_audit_complete",
        "device_commands",
        schema="wes_biz",
        type_="check",
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
        "AND device_binding_id IS NULL AND endpoint_base_url IS NOT NULL "
        "AND command_timeout_ms IS NOT NULL) OR "
        "(execution_ref_type <> 'MANUAL_DEBUG' AND line_run_epoch_id IS NOT NULL "
        "AND device_binding_id IS NOT NULL AND endpoint_base_url IS NULL "
        "AND command_timeout_ms IS NULL))",
        schema="wes_biz",
    )
    op.drop_column("device_commands", "execution_reason", schema="wes_biz")
