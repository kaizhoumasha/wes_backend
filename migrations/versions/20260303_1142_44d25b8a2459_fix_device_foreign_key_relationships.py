"""Fix device foreign key relationships

Revision ID: 44d25b8a2459
Revises: 3db05fc3930f
Create Date: 2026-03-03 11:42:59.533916+08:00

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "44d25b8a2459"
down_revision: Union[str, Sequence[str], None] = "3db05fc3930f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""


def downgrade() -> None:
    """Downgrade schema."""
