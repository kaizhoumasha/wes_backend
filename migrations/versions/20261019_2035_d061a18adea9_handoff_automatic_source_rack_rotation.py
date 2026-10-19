"""接续自动来源架换面与当前投料轮次。

Revision ID: d061a18adea9
Revises: 20d588a3a234
"""

import sqlalchemy as sa
from alembic import op

revision = "d061a18adea9"
down_revision = "20d588a3a234"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "automatic_picking_return_motions", sa.Column("rotation_transport_task_id", sa.String(80)), schema="wes_biz"
    )
    op.add_column(
        "automatic_picking_return_motions", sa.Column("rotation_result_evidence_id", sa.BigInteger()), schema="wes_biz"
    )
    op.create_unique_constraint(
        "ux_auto_motion_rotation_transport",
        "automatic_picking_return_motions",
        ["rotation_transport_task_id"],
        schema="wes_biz",
    )
    op.create_foreign_key(
        "fk_auto_motion_rotation_result",
        "automatic_picking_return_motions",
        "inbound_evidences",
        ["rotation_result_evidence_id"],
        ["id"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )
    for column in (
        sa.Column("inbound_client_request_id", sa.String(120)),
        sa.Column("round_started_at", sa.DateTime()),
        sa.Column("round_causal_token", sa.BigInteger()),
        sa.Column("source_arrival_evidence_id", sa.BigInteger()),
    ):
        op.add_column("automatic_picking_feeds", column, schema="wes_biz")
    op.create_foreign_key(
        "fk_auto_feed_source_arrival",
        "automatic_picking_feeds",
        "inbound_evidences",
        ["source_arrival_evidence_id"],
        ["id"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )
    op.create_check_constraint(
        "automatic_feed_source_round",
        "automatic_picking_feeds",
        "(inbound_client_request_id IS NULL AND round_started_at IS NULL AND round_causal_token IS NULL AND source_arrival_evidence_id IS NULL) OR (inbound_client_request_id IS NOT NULL AND round_started_at IS NOT NULL AND round_causal_token IS NOT NULL AND round_causal_token > 0 AND source_arrival_evidence_id IS NOT NULL)",
        schema="wes_biz",
    )


def downgrade():
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS(SELECT 1 FROM wes_biz.automatic_picking_return_motions WHERE rotation_transport_task_id IS NOT NULL OR rotation_result_evidence_id IS NOT NULL) OR EXISTS(SELECT 1 FROM wes_biz.automatic_picking_feeds WHERE inbound_client_request_id IS NOT NULL)"
            )
        )
        .scalar()
    ):
        raise RuntimeError("cannot discard automatic source rotation handoff")
    op.drop_constraint("automatic_feed_source_round", "automatic_picking_feeds", schema="wes_biz", type_="check")
    op.drop_constraint("fk_auto_feed_source_arrival", "automatic_picking_feeds", schema="wes_biz", type_="foreignkey")
    for name in ("source_arrival_evidence_id", "round_causal_token", "round_started_at", "inbound_client_request_id"):
        op.drop_column("automatic_picking_feeds", name, schema="wes_biz")
    op.drop_constraint(
        "fk_auto_motion_rotation_result", "automatic_picking_return_motions", schema="wes_biz", type_="foreignkey"
    )
    op.drop_column("automatic_picking_return_motions", "rotation_result_evidence_id", schema="wes_biz")
    op.drop_constraint(
        "ux_auto_motion_rotation_transport", "automatic_picking_return_motions", schema="wes_biz", type_="unique"
    )
    op.drop_column("automatic_picking_return_motions", "rotation_transport_task_id", schema="wes_biz")
