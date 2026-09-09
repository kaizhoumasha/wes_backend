"""保留被改正的 WMS prepare 请求

Revision ID: 3abf401aebaa
Revises: 133712f6a89a
Create Date: 2026-09-09 23:49:36.669781+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3abf401aebaa"
down_revision: Union[str, Sequence[str], None] = "133712f6a89a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_wms_confirmations_wms_confirmation_status_valid"),
        "wms_confirmations",
        schema="wes_biz",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_wms_confirmations_wms_confirmation_status_valid"),
        "wms_confirmations",
        "status IN ('PENDING', 'DISPATCHING', 'COMPLETED', 'RECONCILING', 'SUPERSEDED')",
        schema="wes_biz",
    )
    op.drop_index("ux_wms_confirmations_picking_task_prepare", table_name="wms_confirmations", schema="wes_biz")
    op.create_index(
        "ux_wms_confirmations_picking_task_prepare",
        "wms_confirmations",
        ["picking_task_id", "operation"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text(
            "picking_task_id IS NOT NULL AND operation = 'outbound.picking_task.prepare@v1' AND status <> 'SUPERSEDED'"
        ),
    )


def downgrade() -> None:
    op.drop_index("ux_wms_confirmations_picking_task_prepare", table_name="wms_confirmations", schema="wes_biz")
    op.create_index(
        "ux_wms_confirmations_picking_task_prepare",
        "wms_confirmations",
        ["picking_task_id", "operation"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("picking_task_id IS NOT NULL AND operation = 'outbound.picking_task.prepare@v1'"),
    )
    op.drop_constraint(
        op.f("ck_wms_confirmations_wms_confirmation_status_valid"),
        "wms_confirmations",
        schema="wes_biz",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_wms_confirmations_wms_confirmation_status_valid"),
        "wms_confirmations",
        "status IN ('PENDING', 'DISPATCHING', 'COMPLETED', 'RECONCILING')",
        schema="wes_biz",
    )
