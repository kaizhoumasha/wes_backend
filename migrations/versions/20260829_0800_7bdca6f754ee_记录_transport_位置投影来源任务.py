"""记录 Transport 位置投影来源任务

Revision ID: 7bdca6f754ee
Revises: 71eeea05c864
Create Date: 2026-08-29 08:00:02.168789+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7bdca6f754ee"
down_revision: Union[str, Sequence[str], None] = "71eeea05c864"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "transport_position_projections",
        sa.Column("source_transport_task_id", sa.String(length=80), nullable=True),
        schema="wes_runtime",
    )
    op.create_index(
        "ix_transport_position_projection_source_task",
        "transport_position_projections",
        ["source_transport_task_id"],
        unique=False,
        schema="wes_runtime",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_transport_position_projection_source_task",
        table_name="transport_position_projections",
        schema="wes_runtime",
    )
    op.drop_column("transport_position_projections", "source_transport_task_id", schema="wes_runtime")
