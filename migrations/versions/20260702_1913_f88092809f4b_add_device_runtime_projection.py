"""add device runtime projection

Revision ID: f88092809f4b
Revises: 629d8e64eb13
Create Date: 2026-07-02 19:13:16.245980+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

SCHEMA = "wes_runtime"

# revision identifiers, used by Alembic.
revision: str = "f88092809f4b"
down_revision: Union[str, Sequence[str], None] = "629d8e64eb13"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "device_runtime_projections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=True),
        sa.Column("device_code", sa.String(length=100), nullable=False),
        sa.Column("workline_id", sa.Integer(), nullable=True),
        sa.Column("device_role", sa.String(length=50), nullable=True),
        sa.Column("provider_code", sa.String(length=50), nullable=True),
        sa.Column("runtime_status", sa.String(length=20), nullable=False),
        sa.Column("current_command_id", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("maintenance_mode", sa.Boolean(), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("status_observed_at", sa.DateTime(), nullable=False),
        sa.Column("status_valid_until", sa.DateTime(), nullable=False),
        sa.Column("in_flight_count", sa.Integer(), nullable=False),
        sa.Column("concurrency_limit", sa.Integer(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "runtime_status IN ('IDLE', 'RUNNING', 'ERROR', 'OFFLINE', 'UNKNOWN', 'MAINTENANCE')",
            name="ck_wes_runtime_device_runtime_projections_status",
        ),
        sa.CheckConstraint(
            "concurrency_limit >= 1",
            name="ck_wes_runtime_device_runtime_projections_concurrency_limit",
        ),
        sa.CheckConstraint(
            "in_flight_count >= 0",
            name="ck_wes_runtime_device_runtime_projections_in_flight_count",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wes_runtime_device_runtime_projections_current_command_id",
        "device_runtime_projections",
        ["current_command_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wes_runtime_device_runtime_projections_device_id",
        "device_runtime_projections",
        ["device_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wes_runtime_device_runtime_projections_device_role",
        "device_runtime_projections",
        ["device_role"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wes_runtime_device_runtime_projections_provider_code",
        "device_runtime_projections",
        ["provider_code"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wes_runtime_device_runtime_projections_runtime_status",
        "device_runtime_projections",
        ["runtime_status"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wes_runtime_device_runtime_projections_workline_id",
        "device_runtime_projections",
        ["workline_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wes_runtime_device_runtime_projections_workline_status",
        "device_runtime_projections",
        ["workline_id", "runtime_status"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ux_wes_runtime_device_runtime_projections_device_code",
        "device_runtime_projections",
        ["device_code"],
        unique=True,
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ux_wes_runtime_device_runtime_projections_device_code",
        table_name="device_runtime_projections",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_wes_runtime_device_runtime_projections_workline_status",
        table_name="device_runtime_projections",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_wes_runtime_device_runtime_projections_workline_id",
        table_name="device_runtime_projections",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_wes_runtime_device_runtime_projections_runtime_status",
        table_name="device_runtime_projections",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_wes_runtime_device_runtime_projections_provider_code",
        table_name="device_runtime_projections",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_wes_runtime_device_runtime_projections_device_role",
        table_name="device_runtime_projections",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_wes_runtime_device_runtime_projections_device_id",
        table_name="device_runtime_projections",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_wes_runtime_device_runtime_projections_current_command_id",
        table_name="device_runtime_projections",
        schema=SCHEMA,
    )
    op.drop_table("device_runtime_projections", schema=SCHEMA)
