"""numeric bin cell depth

Revision ID: 1bda271cfeb5
Revises: ec493e8e53a1
Create Date: 2026-06-02 07:30:51.107899+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1bda271cfeb5"
down_revision: Union[str, Sequence[str], None] = "ec493e8e53a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "resource_bin_cell_occupancies",
        "used_depth_mm",
        existing_type=sa.Float(),
        type_=sa.Numeric(12, 3),
        existing_nullable=False,
        schema="wes_biz",
    )
    op.alter_column(
        "resource_bin_cell_occupancies",
        "capacity_depth_mm",
        existing_type=sa.Float(),
        type_=sa.Numeric(12, 3),
        existing_nullable=True,
        schema="wes_biz",
    )
    op.alter_column(
        "resource_bin_cell_occupancies",
        "remaining_depth_mm",
        existing_type=sa.Float(),
        type_=sa.Numeric(12, 3),
        existing_nullable=True,
        schema="wes_biz",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        "resource_bin_cell_occupancies",
        "remaining_depth_mm",
        existing_type=sa.Numeric(12, 3),
        type_=sa.Float(),
        existing_nullable=True,
        schema="wes_biz",
    )
    op.alter_column(
        "resource_bin_cell_occupancies",
        "capacity_depth_mm",
        existing_type=sa.Numeric(12, 3),
        type_=sa.Float(),
        existing_nullable=True,
        schema="wes_biz",
    )
    op.alter_column(
        "resource_bin_cell_occupancies",
        "used_depth_mm",
        existing_type=sa.Numeric(12, 3),
        type_=sa.Float(),
        existing_nullable=False,
        schema="wes_biz",
    )
