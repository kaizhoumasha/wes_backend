"""持久化本轮清退拒绝退避；不修改原物理任务或证据。"""

import sqlalchemy as sa
from alembic import op

revision = "a72d4e6c9b13"
down_revision = "c6e209af714b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "manual_picking_departure_retries",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("workline_id", sa.BigInteger(), nullable=False),
        sa.Column("scope_identity", sa.String(256), nullable=False),
        sa.Column("rack_id", sa.String(160), nullable=False),
        sa.Column("step", sa.String(96), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("last_transport_task_id", sa.String(80), nullable=False),
        sa.Column("last_transport_causal_token", sa.BigInteger(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=False),
        sa.Column("decision_operation_id", sa.String(160), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["workline_id"], ["wes_biz.work_lines.id"]),
        sa.UniqueConstraint("workline_id", "scope_identity", "rack_id", "step", name="ux_manual_departure_retry_scope"),
        sa.CheckConstraint(
            "retry_count BETWEEN 1 AND 7 AND last_transport_causal_token > 0", name="manual_departure_retry_positive"
        ),
        schema="wes_biz",
    )


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM wes_biz.manual_picking_departure_retries)")).scalar():
        raise RuntimeError("departure retry cursors exist; downgrade would discard durable backoff")
    op.drop_table("manual_picking_departure_retries", schema="wes_biz")
