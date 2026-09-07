"""scope picking confirmation uniqueness to prepare

Revision ID: 5d3e6e4df5be
Revises: 3d040b37c049
Create Date: 2026-09-07 04:00:37.872097+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5d3e6e4df5be"
down_revision: Union[str, Sequence[str], None] = "3d040b37c049"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ux_wms_confirmations_picking_task_operation", table_name="wms_confirmations", schema="wes_biz")
    op.create_index(
        "ux_wms_confirmations_picking_task_prepare",
        "wms_confirmations",
        ["picking_task_id", "operation"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("picking_task_id IS NOT NULL AND operation = 'outbound.picking_task.prepare@v1'"),
    )


def downgrade() -> None:
    op.drop_index("ux_wms_confirmations_picking_task_prepare", table_name="wms_confirmations", schema="wes_biz")
    op.create_index(
        "ux_wms_confirmations_picking_task_operation",
        "wms_confirmations",
        ["picking_task_id", "operation"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("picking_task_id IS NOT NULL"),
    )
