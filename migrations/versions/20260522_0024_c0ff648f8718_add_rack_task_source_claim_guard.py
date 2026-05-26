"""retired rack source claim draft before rack domain consolidation

Revision ID: c0ff648f8718
Revises: 97dbf218ed9f
Create Date: 2026-05-22 00:24:58.813086+08:00

"""

from collections.abc import Sequence
from typing import Union

# revision identifiers, used by Alembic.
revision: str = "c0ff648f8718"
down_revision: Union[str, Sequence[str], None] = "97dbf218ed9f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op: source claim guard is created on rack_tasks by the system rack migration."""


def downgrade() -> None:
    """No-op."""
