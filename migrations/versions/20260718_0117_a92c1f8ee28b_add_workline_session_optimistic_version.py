"""add workline session optimistic version

Revision ID: a92c1f8ee28b
Revises: fa15ba0aef65
Create Date: 2026-07-18 01:17:17.116198+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a92c1f8ee28b"
down_revision: Union[str, Sequence[str], None] = "fa15ba0aef65"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "workline_sessions",
        sa.Column("version", sa.Integer(), server_default="0", nullable=False, comment="版本号"),
        schema="wes_biz",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("workline_sessions", "version", schema="wes_biz")
