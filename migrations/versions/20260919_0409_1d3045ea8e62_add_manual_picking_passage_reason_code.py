"""add_manual_picking_passage_reason_code

Revision ID: 1d3045ea8e62
Revises: d8fac9644646
Create Date: 2026-09-19 04:09:11.690451+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1d3045ea8e62"
down_revision: Union[str, Sequence[str], None] = "d8fac9644646"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "manual_picking_passages",
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        schema="wes_biz",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("manual_picking_passages", "reason_code", schema="wes_biz")
