"""add ingress count to workline sessions

Revision ID: e2f4a6b8c9d0
Revises: d1e2f3a4b5c6
Create Date: 2026-04-17 11:15:00.000000+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e2f4a6b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "workline_sessions",
        sa.Column("ingress_count", sa.Integer(), nullable=False, server_default=sa.text("1")),
        schema="wes_biz",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("workline_sessions", "ingress_count", schema="wes_biz")
