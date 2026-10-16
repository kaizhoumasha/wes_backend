"""保存自动回程首次 SCAN4 事实及 FIFO 运行投影。

Revision ID: d028d7c65eb7
Revises: bb07c44fb54f
"""

import sqlalchemy as sa
from alembic import op

revision = "d028d7c65eb7"
down_revision = "bb07c44fb54f"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("scan4_evidence_id", "scan4_event_time"):
        op.add_column("automatic_picking_returns", sa.Column(name, sa.BigInteger(), nullable=True), schema="wes_biz")
    op.create_foreign_key(
        op.f("fk_automatic_picking_returns_scan4_evidence_id_inbound_evidences"),
        "automatic_picking_returns",
        "inbound_evidences",
        ["scan4_evidence_id"],
        ["id"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )
    op.create_unique_constraint(
        op.f("uq_automatic_picking_returns_scan4_evidence_id"),
        "automatic_picking_returns",
        ["scan4_evidence_id"],
        schema="wes_biz",
    )
    op.create_check_constraint(
        "automatic_return_scan4_pair",
        "automatic_picking_returns",
        "(scan4_evidence_id IS NULL AND scan4_event_time IS NULL) OR "
        "(scan4_evidence_id IS NOT NULL AND scan4_event_time IS NOT NULL AND scan4_event_time > 0)",
        schema="wes_biz",
    )
    op.create_index(
        "ix_automatic_return_current_fifo",
        "automatic_picking_returns",
        ["workline_id", "scan4_event_time"],
        schema="wes_biz",
        postgresql_where=sa.text("archived_at IS NULL AND scan4_event_time IS NOT NULL"),
    )


def downgrade():
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM wes_biz.automatic_picking_returns "
                "WHERE scan4_evidence_id IS NOT NULL OR scan4_event_time IS NOT NULL)"
            )
        )
        .scalar()
    ):
        raise RuntimeError("cannot discard first SCAN4 facts or FIFO order")
    op.drop_index("ix_automatic_return_current_fifo", table_name="automatic_picking_returns", schema="wes_biz")
    op.drop_constraint("automatic_return_scan4_pair", "automatic_picking_returns", schema="wes_biz", type_="check")
    op.drop_constraint(
        op.f("uq_automatic_picking_returns_scan4_evidence_id"),
        "automatic_picking_returns",
        schema="wes_biz",
        type_="unique",
    )
    op.drop_constraint(
        op.f("fk_automatic_picking_returns_scan4_evidence_id_inbound_evidences"),
        "automatic_picking_returns",
        schema="wes_biz",
        type_="foreignkey",
    )
    op.drop_column("automatic_picking_returns", "scan4_event_time", schema="wes_biz")
    op.drop_column("automatic_picking_returns", "scan4_evidence_id", schema="wes_biz")
