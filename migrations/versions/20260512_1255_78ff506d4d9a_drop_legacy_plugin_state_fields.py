"""drop legacy plugin state fields

Revision ID: 78ff506d4d9a
Revises: 3a31e15009d7
Create Date: 2026-05-12 12:55:00.569497+08:00

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "78ff506d4d9a"
down_revision: Union[str, Sequence[str], None] = "3a31e15009d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_index("ix_wes_biz_device_commands_issued_plugin_state", table_name="device_commands", schema="wes_biz")
    op.drop_index("ix_wes_biz_workline_sessions_plugin_state", table_name="workline_sessions", schema="wes_biz")
    op.drop_column("device_commands", "issued_plugin_state", schema="wes_biz")
    op.drop_column("workline_sessions", "current_wait_token", schema="wes_biz")
    op.drop_column("workline_sessions", "plugin_state", schema="wes_biz")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        "workline_sessions",
        sa.Column("plugin_state", sa.String(length=100), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "workline_sessions",
        sa.Column("current_wait_token", sa.String(length=200), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "device_commands",
        sa.Column("issued_plugin_state", sa.String(length=100), nullable=True),
        schema="wes_biz",
    )
    op.create_index(
        "ix_wes_biz_workline_sessions_plugin_state",
        "workline_sessions",
        ["plugin_state"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        "ix_wes_biz_device_commands_issued_plugin_state",
        "device_commands",
        ["issued_plugin_state"],
        unique=False,
        schema="wes_biz",
    )
