"""持久引用自动拣料目标架 departure 当前请求。"""

import sqlalchemy as sa
from alembic import op

revision = "8c9d01122445"
down_revision = "b514fdcfbefc"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "automatic_picking_pickups", sa.Column("target_departure_confirmation_id", sa.BigInteger()), schema="wes_biz"
    )
    op.add_column("automatic_picking_pickups", sa.Column("target_departure_round_id", sa.String(120)), schema="wes_biz")
    op.add_column("automatic_picking_pickups", sa.Column("target_departure_retry_at", sa.DateTime()), schema="wes_biz")
    op.create_foreign_key(
        "fk_auto_pickup_departure_current",
        "automatic_picking_pickups",
        "wms_confirmations",
        ["target_departure_confirmation_id"],
        ["id"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )


def downgrade():
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM wes_biz.automatic_picking_pickups WHERE "
                "target_departure_confirmation_id IS NOT NULL OR target_departure_round_id IS NOT NULL OR "
                "target_departure_retry_at IS NOT NULL)"
            )
        )
        .scalar()
    ):
        raise RuntimeError("cannot discard automatic target departure current")
    op.drop_constraint(
        "fk_auto_pickup_departure_current", "automatic_picking_pickups", schema="wes_biz", type_="foreignkey"
    )
    for name in ("target_departure_retry_at", "target_departure_round_id", "target_departure_confirmation_id"):
        op.drop_column("automatic_picking_pickups", name, schema="wes_biz")
