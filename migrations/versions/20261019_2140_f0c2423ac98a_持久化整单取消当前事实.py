"""持久化整单取消当前事实

Revision ID: f0c2423ac98a
Revises: f72bd038e915
Create Date: 2026-10-19 21:40:24.147172+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f0c2423ac98a"
down_revision: Union[str, Sequence[str], None] = "f72bd038e915"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("picking_tasks", sa.Column("cancelled_at", sa.DateTime(), nullable=True), schema="wes_biz")
    op.execute(
        """
        UPDATE wes_biz.picking_tasks AS task
        SET cancelled_at = proof.cancelled_at
        FROM (
            SELECT picking_task_id, MIN(COALESCE(processed_at, received_at)) AS cancelled_at
            FROM wes_biz.inbound_evidences
            WHERE kind = 'WMS_EVENT'
              AND operation = 'outbound.picking_task.cancel@v1'
              AND apply_status = 'APPLIED'
              AND normalized_payload->'data'->>'cancel_scope' = 'TASK'
            GROUP BY picking_task_id
        ) AS proof
        WHERE task.id = proof.picking_task_id
        """
    )
    # CANCELLED 已是应用成功的业务事实；不从 ARCHIVED 等状态猜测取消。
    op.execute(
        """
        UPDATE wes_biz.picking_tasks
        SET cancelled_at = COALESCE(updated_at, created_at)
        WHERE status = 'CANCELLED' AND cancelled_at IS NULL
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("picking_tasks", "cancelled_at", schema="wes_biz")
