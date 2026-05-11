"""allow workflow ng return items

Revision ID: 5a43d0d64ce1
Revises: 608d8cdb5aa0
Create Date: 2026-05-11 02:01:18.593723+08:00

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5a43d0d64ce1"
down_revision: Union[str, Sequence[str], None] = "608d8cdb5aa0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "ng_return_items",
        "created_from_runtime_hold_id",
        existing_type=sa.Integer(),
        nullable=True,
        schema="wes_biz",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        "ng_return_items",
        "created_from_runtime_hold_id",
        existing_type=sa.Integer(),
        nullable=False,
        schema="wes_biz",
    )
