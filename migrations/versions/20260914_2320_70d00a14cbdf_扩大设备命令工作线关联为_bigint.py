"""扩大设备命令工作线关联为 bigint

Revision ID: 70d00a14cbdf
Revises: e5c5dfb4373f
Create Date: 2026-09-14 23:20:11.862952+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "70d00a14cbdf"
down_revision: Union[str, Sequence[str], None] = "e5c5dfb4373f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "device_commands",
        "workline_id",
        schema="wes_biz",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        "device_commands",
        "workline_id",
        schema="wes_biz",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=True,
    )
