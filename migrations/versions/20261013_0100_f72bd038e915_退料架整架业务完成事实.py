"""退料架整架业务完成当前事实。"""

import sqlalchemy as sa
from alembic import op

revision = "f72bd038e915"
down_revision = "c47a2b9d610e"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "return_rack_task_completions",
        sa.Column("version", sa.Integer(), server_default="0", nullable=False, comment="版本号"),
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间 (UTC)"),
        sa.Column("updated_at", sa.DateTime(), nullable=True, comment="更新时间 (UTC)"),
        sa.Column("id", sa.BigInteger(), nullable=False, primary_key=True, comment="主键 ID"),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人ID"),
        sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人ID"),
        sa.Column("picking_task_id", sa.BigInteger(), sa.ForeignKey("wes_biz.picking_tasks.id"), nullable=False),
        sa.Column("operation_id", sa.String(36), nullable=False),
        sa.Column("rack_id", sa.String(100), nullable=False),
        sa.Column("through_plan_revision", sa.BigInteger(), nullable=False),
        sa.Column("completed_at_ms", sa.BigInteger(), nullable=False),
        sa.Column("source_evidence_id", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("through_plan_revision > 0", name="return_rack_completion_revision_positive"),
        schema="wes_biz",
    )
    op.create_index(
        "ux_return_rack_task_completion",
        "return_rack_task_completions",
        ["picking_task_id", "rack_id"],
        unique=True,
        schema="wes_biz",
    )


def downgrade():
    op.drop_index("ux_return_rack_task_completion", table_name="return_rack_task_completions", schema="wes_biz")
    op.drop_table("return_rack_task_completions", schema="wes_biz")
