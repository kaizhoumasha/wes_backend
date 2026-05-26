"""retired rack task draft before rack domain consolidation

Revision ID: 083e85d1bf93
Revises: b4685be483de
Create Date: 2026-05-20 14:53:17.194398+08:00

"""

from collections.abc import Sequence
from typing import Union

# revision identifiers, used by Alembic.
revision: str = "083e85d1bf93"
down_revision: Union[str, Sequence[str], None] = "b4685be483de"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op: rack tasks are created by the system rack domain migration."""


def downgrade() -> None:
    """No-op."""
