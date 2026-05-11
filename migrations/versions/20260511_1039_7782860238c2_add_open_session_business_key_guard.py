"""add open session business key guard

Revision ID: 7782860238c2
Revises: 5a43d0d64ce1
Create Date: 2026-05-11 10:39:42.693158+08:00

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7782860238c2"
down_revision: Union[str, Sequence[str], None] = "5a43d0d64ce1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OPEN_SESSION_BUSINESS_KEY_WHERE = sa.text(
    "business_key IS NOT NULL AND status IN "
    "('NEW', 'RUNNING', 'WAITING_DEVICE_RESULT', 'WAITING_EXTERNAL', 'MANUAL_HOLD')"
)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        "uq_workline_sessions_open_business_key",
        "workline_sessions",
        ["workline_id", "business_key"],
        unique=True,
        schema="wes_biz",
        postgresql_where=_OPEN_SESSION_BUSINESS_KEY_WHERE,
        sqlite_where=_OPEN_SESSION_BUSINESS_KEY_WHERE,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "uq_workline_sessions_open_business_key",
        table_name="workline_sessions",
        schema="wes_biz",
    )
