"""add workline contract snapshots

Revision ID: 7a2c4d6e8f10
Revises: c6f8e1a2b4d9
Create Date: 2026-03-29 11:00:00+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7a2c4d6e8f10"
down_revision: Union[str, Sequence[str], None] = "c6f8e1a2b4d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _recreate_device_event_type_constraint(*, include_inspection_completed: bool) -> None:
    allowed_types = [
        "ESTOP_PRESSED",
        "DEVICE_ONLINE",
        "DEVICE_OFFLINE",
        "DEVICE_ERROR",
        "MATERIAL_ARRIVED",
        "SCAN_COMPLETED",
        "PICK_COMPLETED",
        "PUT_COMPLETED",
        "PROCESS_COMPLETED",
    ]
    if include_inspection_completed:
        allowed_types.insert(6, "INSPECTION_COMPLETED")

    allowed_types_sql = ",\n                ".join(f"'{event_type}'" for event_type in allowed_types)

    op.execute(
        """
        ALTER TABLE wes_biz.device_event_logs
        DROP CONSTRAINT IF EXISTS eventtype
        """
    )
    op.execute(
        """
        ALTER TABLE wes_biz.device_event_logs
        DROP CONSTRAINT IF EXISTS ck_device_event_logs_eventtype
        """
    )
    op.execute(
        f"""
        ALTER TABLE wes_biz.device_event_logs
        ADD CONSTRAINT eventtype
        CHECK (
            event_type IN (
                {allowed_types_sql}
            )
        )
        """
    )


def upgrade() -> None:
    """Upgrade schema."""

    op.add_column("devices", sa.Column("plugin_key", sa.String(length=100), nullable=True), schema="wes_biz")
    op.add_column("devices", sa.Column("contract_profile", sa.String(length=100), nullable=True), schema="wes_biz")
    op.add_column("devices", sa.Column("contract_version", sa.String(length=50), nullable=True), schema="wes_biz")
    op.create_index(op.f("ix_wes_biz_devices_plugin_key"), "devices", ["plugin_key"], unique=False, schema="wes_biz")
    op.execute(
        """
        UPDATE wes_biz.devices AS d
        SET plugin_key = w.plugin_key,
            contract_profile = COALESCE(d.contract_profile, 'default'),
            contract_version = COALESCE(d.contract_version, '1.0')
        FROM wes_biz.work_lines AS w
        WHERE d.work_line_id = w.id
          AND w.plugin_key = 'smt_classifier'
          AND (
              d.plugin_key IS DISTINCT FROM w.plugin_key
              OR d.contract_profile IS NULL
              OR d.contract_version IS NULL
          )
        """
    )

    op.add_column("device_commands", sa.Column("plugin_key", sa.String(length=100), nullable=True), schema="wes_biz")
    op.add_column(
        "device_commands",
        sa.Column("contract_version", sa.String(length=50), nullable=True),
        schema="wes_biz",
    )
    op.add_column("device_commands", sa.Column("step_code", sa.String(length=100), nullable=True), schema="wes_biz")
    op.create_index(
        op.f("ix_wes_biz_device_commands_plugin_key"),
        "device_commands",
        ["plugin_key"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_device_commands_step_code"),
        "device_commands",
        ["step_code"],
        unique=False,
        schema="wes_biz",
    )

    op.add_column("device_event_logs", sa.Column("plugin_key", sa.String(length=100), nullable=True), schema="wes_biz")
    op.add_column(
        "device_event_logs",
        sa.Column("contract_version", sa.String(length=50), nullable=True),
        schema="wes_biz",
    )
    op.add_column("device_event_logs", sa.Column("step_code", sa.String(length=100), nullable=True), schema="wes_biz")
    op.add_column("device_event_logs", sa.Column("ack_code", sa.String(length=50), nullable=True), schema="wes_biz")
    op.add_column(
        "device_event_logs",
        sa.Column("ack_message", sa.String(length=500), nullable=True),
        schema="wes_biz",
    )
    op.add_column("device_event_logs", sa.Column("validation_error", sa.Text(), nullable=True), schema="wes_biz")
    op.create_index(
        op.f("ix_wes_biz_device_event_logs_plugin_key"),
        "device_event_logs",
        ["plugin_key"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_device_event_logs_step_code"),
        "device_event_logs",
        ["step_code"],
        unique=False,
        schema="wes_biz",
    )

    op.add_column(
        "workline_sessions",
        sa.Column("contract_version", sa.String(length=50), nullable=True),
        schema="wes_biz",
    )
    op.add_column("workline_sessions", sa.Column("step_code", sa.String(length=100), nullable=True), schema="wes_biz")
    op.create_index(
        op.f("ix_wes_biz_workline_sessions_step_code"),
        "workline_sessions",
        ["step_code"],
        unique=False,
        schema="wes_biz",
    )

    _recreate_device_event_type_constraint(include_inspection_completed=True)


def downgrade() -> None:
    """Downgrade schema."""

    _recreate_device_event_type_constraint(include_inspection_completed=False)

    op.drop_index(op.f("ix_wes_biz_workline_sessions_step_code"), table_name="workline_sessions", schema="wes_biz")
    op.drop_column("workline_sessions", "step_code", schema="wes_biz")
    op.drop_column("workline_sessions", "contract_version", schema="wes_biz")

    op.drop_index(op.f("ix_wes_biz_device_event_logs_step_code"), table_name="device_event_logs", schema="wes_biz")
    op.drop_index(op.f("ix_wes_biz_device_event_logs_plugin_key"), table_name="device_event_logs", schema="wes_biz")
    op.drop_column("device_event_logs", "validation_error", schema="wes_biz")
    op.drop_column("device_event_logs", "ack_message", schema="wes_biz")
    op.drop_column("device_event_logs", "ack_code", schema="wes_biz")
    op.drop_column("device_event_logs", "step_code", schema="wes_biz")
    op.drop_column("device_event_logs", "contract_version", schema="wes_biz")
    op.drop_column("device_event_logs", "plugin_key", schema="wes_biz")

    op.drop_index(op.f("ix_wes_biz_device_commands_step_code"), table_name="device_commands", schema="wes_biz")
    op.drop_index(op.f("ix_wes_biz_device_commands_plugin_key"), table_name="device_commands", schema="wes_biz")
    op.drop_column("device_commands", "step_code", schema="wes_biz")
    op.drop_column("device_commands", "contract_version", schema="wes_biz")
    op.drop_column("device_commands", "plugin_key", schema="wes_biz")

    op.drop_index(op.f("ix_wes_biz_devices_plugin_key"), table_name="devices", schema="wes_biz")
    op.drop_column("devices", "contract_version", schema="wes_biz")
    op.drop_column("devices", "contract_profile", schema="wes_biz")
    op.drop_column("devices", "plugin_key", schema="wes_biz")
