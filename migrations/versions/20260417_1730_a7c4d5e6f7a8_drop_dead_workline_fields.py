"""drop dead workline fields

Revision ID: a7c4d5e6f7a8
Revises: f3a5b7c9d1e2
Create Date: 2026-04-17 17:30:00.000000+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c4d5e6f7a8"
down_revision: Union[str, Sequence[str], None] = "f3a5b7c9d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column("workline_sessions", "retry_count", schema="wes_biz")
    op.drop_column("workline_sessions", "last_decision_id", schema="wes_biz")
    op.drop_column("workline_timelines", "related_external_call_id", schema="wes_biz")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        "workline_timelines",
        sa.Column("related_external_call_id", sa.Integer(), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "workline_sessions",
        sa.Column("last_decision_id", sa.Integer(), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "workline_sessions",
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        schema="wes_biz",
    )
