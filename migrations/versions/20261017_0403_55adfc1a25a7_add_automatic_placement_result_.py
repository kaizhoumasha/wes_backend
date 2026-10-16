"""保存自动单盘的确定位置 Evidence 与报告义务引用。

Revision ID: 55adfc1a25a7
Revises: 4e30101440d2
"""

import sqlalchemy as sa
from alembic import op

revision = "55adfc1a25a7"
down_revision = "4e30101440d2"
branch_labels = None
depends_on = None


def upgrade():
    for column, target in (
        ("put_result_evidence_id", "inbound_evidences"),
        ("movement_confirmation_id", "wms_confirmations"),
    ):
        op.add_column("automatic_picking_pickups", sa.Column(column, sa.BigInteger(), nullable=True), schema="wes_biz")
        op.create_foreign_key(
            op.f("fk_automatic_picking_pickups_" + column + "_" + target),
            "automatic_picking_pickups",
            target,
            [column],
            ["id"],
            source_schema="wes_biz",
            referent_schema="wes_biz",
        )
        op.create_unique_constraint(
            op.f("uq_automatic_picking_pickups_" + column), "automatic_picking_pickups", [column], schema="wes_biz"
        )
    op.create_check_constraint(
        "automatic_pickup_placement_pair",
        "automatic_picking_pickups",
        "(put_result_evidence_id IS NULL AND movement_confirmation_id IS NULL) OR "
        "(put_command_code IS NOT NULL AND put_result_evidence_id IS NOT NULL AND movement_confirmation_id IS NOT NULL)",
        schema="wes_biz",
    )


def downgrade():
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM wes_biz.automatic_picking_pickups WHERE put_result_evidence_id IS NOT NULL "
                "OR movement_confirmation_id IS NOT NULL)"
            )
        )
        .scalar()
    ):
        raise RuntimeError("cannot discard confirmed placement/report references")
    op.drop_constraint("automatic_pickup_placement_pair", "automatic_picking_pickups", schema="wes_biz", type_="check")
    for column in ("movement_confirmation_id", "put_result_evidence_id"):
        op.drop_constraint(
            op.f("uq_automatic_picking_pickups_" + column),
            "automatic_picking_pickups",
            schema="wes_biz",
            type_="unique",
        )
        op.drop_constraint(
            op.f(
                "fk_automatic_picking_pickups_"
                + column
                + "_"
                + ("wms_confirmations" if column == "movement_confirmation_id" else "inbound_evidences")
            ),
            "automatic_picking_pickups",
            schema="wes_biz",
            type_="foreignkey",
        )
        op.drop_column("automatic_picking_pickups", column, schema="wes_biz")
