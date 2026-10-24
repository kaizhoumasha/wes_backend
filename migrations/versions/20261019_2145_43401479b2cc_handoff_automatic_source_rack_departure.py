"""持久接续自动来源架离场。

Revision ID: 43401479b2cc
Revises: d061a18adea9
"""

import sqlalchemy as sa
from alembic import op

revision = "43401479b2cc"
down_revision = "d061a18adea9"
branch_labels = None
depends_on = None


def upgrade():
    for column in (
        sa.Column("departure_operation_id", sa.String(120)),
        sa.Column("departure_transport_task_id", sa.String(80)),
        sa.Column("departure_result_evidence_id", sa.BigInteger()),
    ):
        op.add_column("automatic_picking_return_motions", column, schema="wes_biz")
    op.create_unique_constraint(
        "ux_auto_motion_departure_operation",
        "automatic_picking_return_motions",
        ["departure_operation_id"],
        schema="wes_biz",
    )
    op.create_unique_constraint(
        "ux_auto_motion_departure_transport",
        "automatic_picking_return_motions",
        ["departure_transport_task_id"],
        schema="wes_biz",
    )
    op.create_foreign_key(
        "fk_auto_motion_departure_result",
        "automatic_picking_return_motions",
        "inbound_evidences",
        ["departure_result_evidence_id"],
        ["id"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )
    op.create_check_constraint(
        "automatic_motion_departure_binding",
        "automatic_picking_return_motions",
        "(departure_operation_id IS NULL AND departure_transport_task_id IS NULL AND departure_result_evidence_id IS NULL) OR (departure_operation_id IS NOT NULL AND departure_transport_task_id IS NOT NULL)",
        schema="wes_biz",
    )


def downgrade():
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS(SELECT 1 FROM wes_biz.automatic_picking_return_motions WHERE departure_operation_id IS NOT NULL)"
            )
        )
        .scalar()
    ):
        raise RuntimeError("cannot discard automatic source departure handoff")
    op.drop_constraint(
        "automatic_motion_departure_binding", "automatic_picking_return_motions", schema="wes_biz", type_="check"
    )
    op.drop_constraint(
        "fk_auto_motion_departure_result", "automatic_picking_return_motions", schema="wes_biz", type_="foreignkey"
    )
    op.drop_constraint(
        "ux_auto_motion_departure_transport", "automatic_picking_return_motions", schema="wes_biz", type_="unique"
    )
    op.drop_constraint(
        "ux_auto_motion_departure_operation", "automatic_picking_return_motions", schema="wes_biz", type_="unique"
    )
    for name in ("departure_result_evidence_id", "departure_transport_task_id", "departure_operation_id"):
        op.drop_column("automatic_picking_return_motions", name, schema="wes_biz")
