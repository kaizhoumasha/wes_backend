"""add workline operation tail index to wms confirmations

Revision ID: 4ce43f66c610
Revises: 2b1adb268fdc
Create Date: 2026-09-16 22:53:12.595844+08:00

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4ce43f66c610"
down_revision: Union[str, Sequence[str], None] = "2b1adb268fdc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        op.f("ix_wes_biz_wms_confirmations_workline_id_operation_operation_id"),
        "wms_confirmations",
        ["workline_id", "operation", "operation_id"],
        schema="wes_biz",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_wes_biz_wms_confirmations_workline_id_operation_operation_id"),
        table_name="wms_confirmations",
        schema="wes_biz",
    )
