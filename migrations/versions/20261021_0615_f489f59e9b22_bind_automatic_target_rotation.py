"""bind automatic target rotation

Revision ID: f489f59e9b22
Revises: c094b575576f
Create Date: 2026-10-21 06:15:22.659208+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f489f59e9b22"
down_revision: Union[str, Sequence[str], None] = "c094b575576f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "automatic_picking_pickups",
        sa.Column("target_rotation_transport_task_id", sa.String(length=80), nullable=True),
        schema="wes_biz",
    )
    op.create_foreign_key(
        "fk_automatic_pickup_target_rotation",
        "automatic_picking_pickups",
        "transport_tasks",
        ["target_rotation_transport_task_id"],
        ["transport_task_id"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )
    op.create_unique_constraint(
        "ux_automatic_pickup_target_rotation",
        "automatic_picking_pickups",
        ["target_rotation_transport_task_id"],
        schema="wes_biz",
    )


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM wes_biz.automatic_picking_pickups "
                "WHERE target_rotation_transport_task_id IS NOT NULL)"
            )
        )
        .scalar()
    ):
        raise RuntimeError("cannot discard automatic target rotation handoff")
    op.drop_constraint(
        "ux_automatic_pickup_target_rotation", "automatic_picking_pickups", schema="wes_biz", type_="unique"
    )
    op.drop_constraint(
        "fk_automatic_pickup_target_rotation", "automatic_picking_pickups", schema="wes_biz", type_="foreignkey"
    )
    op.drop_column("automatic_picking_pickups", "target_rotation_transport_task_id", schema="wes_biz")
