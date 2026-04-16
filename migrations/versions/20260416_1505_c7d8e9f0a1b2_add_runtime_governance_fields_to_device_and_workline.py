"""add runtime governance fields to device and workline

Revision ID: c7d8e9f0a1b2
Revises: a1b2c3d4e5f6
Create Date: 2026-04-16 15:05:00.000000+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "devices",
        sa.Column(
            "capabilities_json",
            postgresql.JSON(astext_type=sa.Text()),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
        schema="wes_biz",
    )
    op.add_column("devices", sa.Column("callback_path", sa.String(length=255), nullable=True), schema="wes_biz")
    op.add_column(
        "devices",
        sa.Column("maintenance_mode", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        schema="wes_biz",
    )
    op.add_column(
        "devices",
        sa.Column(
            "diagnostic_profile",
            postgresql.JSON(astext_type=sa.Text()),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
        schema="wes_biz",
    )
    op.add_column("work_lines", sa.Column("contract_version", sa.String(length=50), nullable=True), schema="wes_biz")
    op.add_column("work_lines", sa.Column("workflow_version", sa.String(length=50), nullable=True), schema="wes_biz")
    op.add_column(
        "work_lines",
        sa.Column(
            "runtime_config_json",
            postgresql.JSON(astext_type=sa.Text()),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
        schema="wes_biz",
    )
    op.add_column("work_lines", sa.Column("owner_team", sa.String(length=100), nullable=True), schema="wes_biz")
    op.add_column("work_lines", sa.Column("support_contact", sa.String(length=100), nullable=True), schema="wes_biz")
    op.add_column(
        "work_lines",
        sa.Column(
            "diagnostic_profile",
            postgresql.JSON(astext_type=sa.Text()),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
        schema="wes_biz",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("work_lines", "diagnostic_profile", schema="wes_biz")
    op.drop_column("work_lines", "support_contact", schema="wes_biz")
    op.drop_column("work_lines", "owner_team", schema="wes_biz")
    op.drop_column("work_lines", "runtime_config_json", schema="wes_biz")
    op.drop_column("work_lines", "workflow_version", schema="wes_biz")
    op.drop_column("work_lines", "contract_version", schema="wes_biz")

    op.drop_column("devices", "diagnostic_profile", schema="wes_biz")
    op.drop_column("devices", "maintenance_mode", schema="wes_biz")
    op.drop_column("devices", "callback_path", schema="wes_biz")
    op.drop_column("devices", "capabilities_json", schema="wes_biz")
