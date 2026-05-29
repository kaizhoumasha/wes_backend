"""add workline inbox hot queue indexes

Revision ID: a6c2c77adabd
Revises: 07be7a97f4a6
Create Date: 2026-05-27 14:34:04.933290+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a6c2c77adabd"
down_revision: Union[str, Sequence[str], None] = "07be7a97f4a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "wes_biz"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        "ix_wes_biz_workline_inbox_new_received_at",
        "workline_inbox",
        ["received_at"],
        unique=False,
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'NEW'"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_wes_biz_workline_inbox_retry_next_retry_received_at",
        "workline_inbox",
        ["next_retry_at", "received_at"],
        unique=False,
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'RETRY'"),
        if_not_exists=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_wes_biz_workline_inbox_retry_next_retry_received_at",
        table_name="workline_inbox",
        schema=SCHEMA,
        if_exists=True,
    )
    op.drop_index(
        "ix_wes_biz_workline_inbox_new_received_at",
        table_name="workline_inbox",
        schema=SCHEMA,
        if_exists=True,
    )
