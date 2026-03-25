"""add command result inbox kind

Revision ID: 9b1a6a4f2d3e
Revises: 4d2d6f0d9d8a
Create Date: 2026-03-25 18:40:00+08:00

"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9b1a6a4f2d3e"
down_revision: Union[str, Sequence[str], None] = "4d2d6f0d9d8a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        ALTER TABLE wes_biz.workline_inbox
        DROP CONSTRAINT IF EXISTS inboxkind
        """
    )
    op.execute(
        """
        ALTER TABLE wes_biz.workline_inbox
        ADD CONSTRAINT inboxkind
        CHECK (
            kind IN (
                'DEVICE_EVENT',
                'COMMAND_RESULT',
                'EXTERNAL_HTTP',
                'TIMER_TIMEOUT',
                'MANUAL_HOLD',
                'MANUAL_RESUME',
                'MANUAL_CANCEL',
                'REPLAY_REQUEST'
            )
        )
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        """
        ALTER TABLE wes_biz.workline_inbox
        DROP CONSTRAINT IF EXISTS inboxkind
        """
    )
    op.execute(
        """
        ALTER TABLE wes_biz.workline_inbox
        ADD CONSTRAINT inboxkind
        CHECK (
            kind IN (
                'DEVICE_EVENT',
                'EXTERNAL_HTTP',
                'TIMER_TIMEOUT',
                'MANUAL_HOLD',
                'MANUAL_RESUME',
                'MANUAL_CANCEL',
                'REPLAY_REQUEST'
            )
        )
        """
    )
