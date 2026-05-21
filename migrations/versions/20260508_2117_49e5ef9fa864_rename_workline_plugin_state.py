"""rename workline plugin state

Revision ID: 49e5ef9fa864
Revises: 2555f6c1b08d
Create Date: 2026-05-08 21:17:30.256089+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "49e5ef9fa864"
down_revision: Union[str, Sequence[str], None] = "2555f6c1b08d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "workline_sessions",
        "step_code",
        new_column_name="plugin_state",
        schema="wes_biz",
        existing_type=sa.String(length=100),
        existing_nullable=True,
    )
    op.alter_column(
        "device_commands",
        "step_code",
        new_column_name="issued_plugin_state",
        schema="wes_biz",
        existing_type=sa.String(length=100),
        existing_nullable=True,
    )
    op.execute(
        """
        ALTER INDEX IF EXISTS wes_biz.ix_wes_biz_workline_sessions_step_code
        RENAME TO ix_wes_biz_workline_sessions_plugin_state
        """
    )
    op.execute(
        """
        ALTER INDEX IF EXISTS wes_biz.ix_wes_biz_device_commands_step_code
        RENAME TO ix_wes_biz_device_commands_issued_plugin_state
        """
    )
    op.execute(
        """
        UPDATE wes_biz.workline_sessions
        SET plugin_state = COALESCE(plugin_state, context_json->>'plugin_state')
        WHERE context_json::jsonb ? 'plugin_state'
        """
    )
    op.execute(
        """
        UPDATE wes_biz.workline_sessions
        SET context_json = (context_json::jsonb - 'plugin_state')::json
        WHERE context_json::jsonb ? 'plugin_state'
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        """
        UPDATE wes_biz.workline_sessions
        SET context_json = jsonb_set(
            COALESCE(context_json::jsonb, '{}'::jsonb),
            '{plugin_state}',
            to_jsonb(plugin_state)
        )::json
        WHERE plugin_state IS NOT NULL
        """
    )
    op.execute(
        """
        ALTER INDEX IF EXISTS wes_biz.ix_wes_biz_device_commands_issued_plugin_state
        RENAME TO ix_wes_biz_device_commands_step_code
        """
    )
    op.execute(
        """
        ALTER INDEX IF EXISTS wes_biz.ix_wes_biz_workline_sessions_plugin_state
        RENAME TO ix_wes_biz_workline_sessions_step_code
        """
    )
    op.alter_column(
        "device_commands",
        "issued_plugin_state",
        new_column_name="step_code",
        schema="wes_biz",
        existing_type=sa.String(length=100),
        existing_nullable=True,
    )
    op.alter_column(
        "workline_sessions",
        "plugin_state",
        new_column_name="step_code",
        schema="wes_biz",
        existing_type=sa.String(length=100),
        existing_nullable=True,
    )
