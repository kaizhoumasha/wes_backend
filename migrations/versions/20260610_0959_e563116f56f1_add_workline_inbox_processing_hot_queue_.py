"""add workline inbox processing hot queue index

Revision ID: e563116f56f1
Revises: 2937b05e1b1c
Create Date: 2026-06-10 09:59:31.081577+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e563116f56f1"
down_revision: Union[str, Sequence[str], None] = "2937b05e1b1c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        "ix_wes_biz_workline_inbox_processing_updated_received_at",
        "workline_inbox",
        ["updated_at", "received_at"],
        unique=False,
        schema="wes_biz",
        postgresql_where=sa.text("status = 'PROCESSING'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_wes_biz_workline_inbox_processing_updated_received_at",
        table_name="workline_inbox",
        schema="wes_biz",
    )
