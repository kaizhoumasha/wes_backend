"""drop device event logs

Revision ID: b9e1c2d3f4a5
Revises: 7a2c4d6e8f10
Create Date: 2026-03-30 10:30:00+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b9e1c2d3f4a5"
down_revision: Union[str, Sequence[str], None] = "7a2c4d6e8f10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    op.execute(
        """
        ALTER TABLE wes_biz.workline_timelines
        DROP CONSTRAINT IF EXISTS workline_timelines_related_event_log_id_fkey
        """
    )
    op.execute(
        """
        ALTER TABLE wes_biz.workline_timelines
        DROP COLUMN IF EXISTS related_event_log_id
        """
    )
    op.execute("DROP TABLE IF EXISTS wes_biz.device_event_logs CASCADE")


def downgrade() -> None:
    """Downgrade schema."""

    op.create_table(
        "device_event_logs",
        sa.Column("deleted_by", sa.Integer(), nullable=True, comment="删除人ID"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True, comment="删除时间"),
        sa.Column("is_deleted", sa.Boolean(), server_default="FALSE", nullable=False, comment="是否已删除"),
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间 (UTC)"),
        sa.Column("updated_at", sa.DateTime(), nullable=True, comment="更新时间 (UTC)"),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键 ID"),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column(
            "event_type",
            sa.Enum(
                "ESTOP_PRESSED",
                "DEVICE_ONLINE",
                "DEVICE_OFFLINE",
                "DEVICE_ERROR",
                "MATERIAL_ARRIVED",
                "SCAN_COMPLETED",
                "INSPECTION_COMPLETED",
                "PICK_COMPLETED",
                "PUT_COMPLETED",
                "PROCESS_COMPLETED",
                name="eventtype",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
        ),
        sa.Column("event_timestamp", sa.DateTime(), nullable=False),
        sa.Column("event_data", sa.JSON(), nullable=True),
        sa.Column("processed", sa.Boolean(), nullable=False),
        sa.Column("processing_result", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.String(length=100), nullable=True),
        sa.Column("session_id", sa.String(length=100), nullable=True),
        sa.Column("workline_id", sa.Integer(), nullable=True),
        sa.Column("plugin_key", sa.String(length=100), nullable=True),
        sa.Column("contract_version", sa.String(length=50), nullable=True),
        sa.Column("step_code", sa.String(length=100), nullable=True),
        sa.Column("ack_code", sa.String(length=50), nullable=True),
        sa.Column("ack_message", sa.String(length=500), nullable=True),
        sa.Column("validation_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["device_id"], ["wes_biz.devices.id"]),
        sa.ForeignKeyConstraint(["workline_id"], ["wes_biz.work_lines.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema="wes_biz",
    )
    op.create_index(op.f("ix_wes_biz_device_event_logs_correlation_id"), "device_event_logs", ["correlation_id"], unique=False, schema="wes_biz")
    op.create_index(op.f("ix_wes_biz_device_event_logs_device_id"), "device_event_logs", ["device_id"], unique=False, schema="wes_biz")
    op.create_index(op.f("ix_wes_biz_device_event_logs_event_timestamp"), "device_event_logs", ["event_timestamp"], unique=False, schema="wes_biz")
    op.create_index(op.f("ix_wes_biz_device_event_logs_event_type"), "device_event_logs", ["event_type"], unique=False, schema="wes_biz")
    op.create_index(op.f("ix_wes_biz_device_event_logs_id"), "device_event_logs", ["id"], unique=True, schema="wes_biz")
    op.create_index(op.f("ix_wes_biz_device_event_logs_plugin_key"), "device_event_logs", ["plugin_key"], unique=False, schema="wes_biz")
    op.create_index(op.f("ix_wes_biz_device_event_logs_processed"), "device_event_logs", ["processed"], unique=False, schema="wes_biz")
    op.create_index(op.f("ix_wes_biz_device_event_logs_session_id"), "device_event_logs", ["session_id"], unique=False, schema="wes_biz")
    op.create_index(op.f("ix_wes_biz_device_event_logs_step_code"), "device_event_logs", ["step_code"], unique=False, schema="wes_biz")
    op.create_index(op.f("ix_wes_biz_device_event_logs_workline_id"), "device_event_logs", ["workline_id"], unique=False, schema="wes_biz")

    op.add_column("workline_timelines", sa.Column("related_event_log_id", sa.Integer(), nullable=True), schema="wes_biz")
    op.create_foreign_key(
        "workline_timelines_related_event_log_id_fkey",
        "workline_timelines",
        "device_event_logs",
        ["related_event_log_id"],
        ["id"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )
