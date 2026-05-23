"""add rack task source claim guard

Revision ID: c0ff648f8718
Revises: 97dbf218ed9f
Create Date: 2026-05-22 00:24:58.813086+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c0ff648f8718"
down_revision: Union[str, Sequence[str], None] = "97dbf218ed9f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "wes_biz"
SOURCE_CLAIM_INDEX_NAME = "ux_workline_rack_tasks_move_source_claim"
SOURCE_CLAIM_WHERE = sa.text(
    "task_type = 'MOVE_RACK' "
    "AND task_status IN ('PLANNED', 'REQUESTED', 'IN_PROGRESS', 'RECONCILING') "
    "AND workline_code IS NOT NULL "
    "AND source_position_code IS NOT NULL "
    "AND rack_code IS NOT NULL"
)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        SOURCE_CLAIM_INDEX_NAME,
        "workline_rack_tasks",
        ["workline_code", "source_position_code", "rack_code"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=SOURCE_CLAIM_WHERE,
        sqlite_where=SOURCE_CLAIM_WHERE,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(SOURCE_CLAIM_INDEX_NAME, table_name="workline_rack_tasks", schema=SCHEMA)
