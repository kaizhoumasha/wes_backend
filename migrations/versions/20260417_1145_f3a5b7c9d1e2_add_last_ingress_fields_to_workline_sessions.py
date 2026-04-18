"""add last ingress fields to workline sessions

Revision ID: f3a5b7c9d1e2
Revises: e2f4a6b8c9d0
Create Date: 2026-04-17 11:45:00.000000+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a5b7c9d1e2"
down_revision: Union[str, Sequence[str], None] = "e2f4a6b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "workline_sessions",
        sa.Column("last_request_id", sa.String(length=200), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "workline_sessions",
        sa.Column("last_ingress_at", sa.DateTime(), nullable=True),
        schema="wes_biz",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("workline_sessions", "last_ingress_at", schema="wes_biz")
    op.drop_column("workline_sessions", "last_request_id", schema="wes_biz")
