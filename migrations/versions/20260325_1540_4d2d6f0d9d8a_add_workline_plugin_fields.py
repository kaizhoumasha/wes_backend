"""add workline plugin fields

Revision ID: 4d2d6f0d9d8a
Revises: ab8b14fe397c
Create Date: 2026-03-25 15:40:00.000000+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4d2d6f0d9d8a"
down_revision: Union[str, Sequence[str], None] = "ab8b14fe397c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "work_lines",
        sa.Column("plugin_key", sa.String(length=100), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "work_lines",
        sa.Column("config", sa.JSON(), nullable=True),
        schema="wes_biz",
    )
    op.execute(sa.text("UPDATE wes_biz.work_lines SET config = '{}'::json WHERE config IS NULL"))
    op.create_index(
        op.f("ix_wes_biz_work_lines_plugin_key"),
        "work_lines",
        ["plugin_key"],
        unique=False,
        schema="wes_biz",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_wes_biz_work_lines_plugin_key"),
        table_name="work_lines",
        schema="wes_biz",
    )
    op.drop_column("work_lines", "config", schema="wes_biz")
    op.drop_column("work_lines", "plugin_key", schema="wes_biz")
