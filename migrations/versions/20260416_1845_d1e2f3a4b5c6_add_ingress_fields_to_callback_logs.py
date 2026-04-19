"""add ingress fields to callback logs

Revision ID: d1e2f3a4b5c6
Revises: c7d8e9f0a1b2
Create Date: 2026-04-16 18:45:00.000000+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "c7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "callback_logs",
        sa.Column("ingress_outcome", sa.String(length=50), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "callback_logs",
        sa.Column("failure_stage", sa.String(length=100), nullable=True),
        schema="wes_biz",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("callback_logs", "failure_stage", schema="wes_biz")
    op.drop_column("callback_logs", "ingress_outcome", schema="wes_biz")
