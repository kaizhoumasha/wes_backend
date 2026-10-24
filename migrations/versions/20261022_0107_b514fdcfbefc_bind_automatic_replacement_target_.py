"""bind automatic replacement target inbound

Revision ID: b514fdcfbefc
Revises: 6a7b8c9d0112
Create Date: 2026-10-22 01:07:42.213076+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b514fdcfbefc"
down_revision: Union[str, Sequence[str], None] = "6a7b8c9d0112"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "automatic_picking_pickups", sa.Column("target_inbound_transport_task_id", sa.String(80)), schema="wes_biz"
    )
    op.create_foreign_key(
        "fk_auto_pickup_target_inbound",
        "automatic_picking_pickups",
        "transport_tasks",
        ["target_inbound_transport_task_id"],
        ["transport_task_id"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM wes_biz.automatic_picking_pickups WHERE target_inbound_transport_task_id IS NOT NULL)"
            )
        )
        .scalar()
    ):
        raise RuntimeError("cannot discard automatic target inbound handoff")
    op.drop_constraint(
        "fk_auto_pickup_target_inbound", "automatic_picking_pickups", schema="wes_biz", type_="foreignkey"
    )
    op.drop_column("automatic_picking_pickups", "target_inbound_transport_task_id", schema="wes_biz")
