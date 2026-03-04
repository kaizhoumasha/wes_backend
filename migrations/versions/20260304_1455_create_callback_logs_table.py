"""placeholder migration to linearize callback_logs branch

Revision ID: 20260304_1455
Revises: 918aa7b0929d
Create Date: 2026-03-04 14:55:00.000000+08:00

"""
from collections.abc import Sequence
from typing import Union

# revision identifiers, used by Alembic.
revision: str = "20260304_1455"
down_revision: Union[str, Sequence[str], None] = "918aa7b0929d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    callback_logs has already been created in earlier revisions.
    Keep this revision as a no-op to preserve revision history while
    maintaining a single linear migration chain.
    """
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
