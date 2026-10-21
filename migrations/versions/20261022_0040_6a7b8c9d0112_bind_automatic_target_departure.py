"""绑定自动 REPLACE 原目标架离场。

Revision ID: 6a7b8c9d0112
Revises: f489f59e9b22
"""

import sqlalchemy as sa
from alembic import op

revision = "6a7b8c9d0112"
down_revision = "f489f59e9b22"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "automatic_picking_pickups", sa.Column("target_departure_transport_task_id", sa.String(80)), schema="wes_biz"
    )
    op.create_foreign_key(
        "fk_auto_pickup_target_departure",
        "automatic_picking_pickups",
        "transport_tasks",
        ["target_departure_transport_task_id"],
        ["transport_task_id"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )


def downgrade():
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM wes_biz.automatic_picking_pickups WHERE target_departure_transport_task_id IS NOT NULL)"
            )
        )
        .scalar()
    ):
        raise RuntimeError("cannot discard automatic target departure handoff")
    op.drop_constraint(
        "fk_auto_pickup_target_departure", "automatic_picking_pickups", schema="wes_biz", type_="foreignkey"
    )
    op.drop_column("automatic_picking_pickups", "target_departure_transport_task_id", schema="wes_biz")
