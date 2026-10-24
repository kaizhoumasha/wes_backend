"""将自动运动前阶段绑定到原进场与来源服务结束事实。

Revision ID: 20d588a3a234
Revises: b1692d740313
"""

import sqlalchemy as sa
from alembic import op

revision = "20d588a3a234"
down_revision = "b1692d740313"
branch_labels = None
depends_on = None


def upgrade():
    for column in (
        sa.Column("inbound_client_request_id", sa.String(120), nullable=True),
        sa.Column("round_started_at", sa.DateTime(), nullable=True),
        sa.Column("round_causal_token", sa.BigInteger(), nullable=True),
        sa.Column("source_feed_id", sa.BigInteger(), nullable=True),
        sa.Column("service_end_evidence_id", sa.BigInteger(), nullable=True),
    ):
        op.add_column("automatic_picking_return_motions", column, schema="wes_biz")
    op.create_foreign_key(
        "fk_auto_motion_service_end",
        "automatic_picking_return_motions",
        "inbound_evidences",
        ["service_end_evidence_id"],
        ["id"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )
    op.create_check_constraint(
        "automatic_return_motion_source_binding",
        "automatic_picking_return_motions",
        "(inbound_client_request_id IS NULL AND round_started_at IS NULL AND round_causal_token IS NULL "
        "AND source_feed_id IS NULL AND service_end_evidence_id IS NULL) OR "
        "(inbound_client_request_id IS NOT NULL AND round_started_at IS NOT NULL AND round_causal_token IS NOT NULL AND round_causal_token > 0 "
        "AND source_feed_id IS NOT NULL AND service_end_evidence_id IS NOT NULL)",
        schema="wes_biz",
    )


def downgrade():
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS(SELECT 1 FROM wes_biz.automatic_picking_return_motions "
                "WHERE inbound_client_request_id IS NOT NULL)"
            )
        )
        .scalar()
    ):
        raise RuntimeError("cannot discard automatic motion evaluation source bindings")
    op.drop_constraint(
        "automatic_return_motion_source_binding", "automatic_picking_return_motions", schema="wes_biz", type_="check"
    )
    op.drop_constraint(
        "fk_auto_motion_service_end", "automatic_picking_return_motions", schema="wes_biz", type_="foreignkey"
    )
    for name in (
        "service_end_evidence_id",
        "source_feed_id",
        "round_causal_token",
        "round_started_at",
        "inbound_client_request_id",
    ):
        op.drop_column("automatic_picking_return_motions", name, schema="wes_biz")
