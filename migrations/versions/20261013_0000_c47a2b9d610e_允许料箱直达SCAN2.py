"""允许有效批次料箱直接由 SCAN2 建立经过，不伪造 SCAN1。"""

import sqlalchemy as sa
from alembic import op

revision = "c47a2b9d610e"
down_revision = "e1c64a8b9d20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_manual_picking_passages_scan2_fifo", table_name="manual_picking_passages", schema="wes_biz")
    op.create_index(
        "ix_manual_picking_passages_current_bin",
        "manual_picking_passages",
        ["workline_id", "task_id", "bin_code"],
        schema="wes_biz",
        postgresql_where=sa.text("disposition <> 'CLOSED'"),
    )
    op.alter_column(
        "manual_picking_passages", "scan1_evidence_id", existing_type=sa.BigInteger(), nullable=True, schema="wes_biz"
    )
    op.alter_column(
        "manual_picking_passages", "scan1_received_at", existing_type=sa.DateTime(), nullable=True, schema="wes_biz"
    )


def downgrade() -> None:
    op.drop_index("ix_manual_picking_passages_current_bin", table_name="manual_picking_passages", schema="wes_biz")
    op.create_index(
        "ix_manual_picking_passages_scan2_fifo",
        "manual_picking_passages",
        ["workline_id", "scan1_received_at", "scan1_evidence_id"],
        schema="wes_biz",
        postgresql_where=sa.text("scan2_evidence_id IS NULL AND disposition <> 'CLOSED'"),
    )
    # 有直达 SCAN2 记录时拒绝降级；不能凭空补造 SCAN1 事实。
    op.alter_column(
        "manual_picking_passages", "scan1_evidence_id", existing_type=sa.BigInteger(), nullable=False, schema="wes_biz"
    )
    op.alter_column(
        "manual_picking_passages", "scan1_received_at", existing_type=sa.DateTime(), nullable=False, schema="wes_biz"
    )
