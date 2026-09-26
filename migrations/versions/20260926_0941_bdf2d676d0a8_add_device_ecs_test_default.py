"""add device ecs_test default

Revision ID: bdf2d676d0a8
Revises: 5bac3de5c2b5
Create Date: 2026-09-26 09:41:18.074128+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bdf2d676d0a8"
down_revision: Union[str, Sequence[str], None] = "5bac3de5c2b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "devices",
        sa.Column("ecs_test_default_json", sa.JSON(), nullable=True),
        schema="wes_biz",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("devices", "ecs_test_default_json", schema="wes_biz")
