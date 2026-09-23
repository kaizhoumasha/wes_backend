"""移除人工料箱任务箱码终态唯一约束

Revision ID: 4ef64c642313
Revises: 9b440473b03d
Create Date: 2026-09-23 22:57:25.943464+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4ef64c642313"
down_revision: Union[str, Sequence[str], None] = "9b440473b03d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ux_manual_picking_passages_wms_terminal", table_name="manual_picking_passages", schema="wes_biz")


def downgrade() -> None:
    op.create_index(
        "ux_manual_picking_passages_wms_terminal",
        "manual_picking_passages",
        ["task_id", "bin_code"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("wms_result IS NOT NULL"),
        sqlite_where=sa.text("wms_result IS NOT NULL"),
    )
