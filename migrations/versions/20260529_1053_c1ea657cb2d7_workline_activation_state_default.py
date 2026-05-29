"""workline activation state default

Revision ID: c1ea657cb2d7
Revises: 4d08cdff2766
Create Date: 2026-05-29 10:53:12.012774+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1ea657cb2d7"
down_revision: Union[str, Sequence[str], None] = "4d08cdff2766"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "work_lines",
        "is_active",
        schema="wes_biz",
        existing_type=sa.Boolean(),
        server_default=sa.false(),
        existing_nullable=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        "work_lines",
        "is_active",
        schema="wes_biz",
        existing_type=sa.Boolean(),
        server_default=None,
        existing_nullable=False,
    )
