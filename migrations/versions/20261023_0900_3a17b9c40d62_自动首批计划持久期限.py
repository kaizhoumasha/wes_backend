"""自动首批计划持久期限。

Revision ID: 3a17b9c40d62
Revises: f5c962df755c
"""

import sqlalchemy as sa
from alembic import op

revision = "3a17b9c40d62"
down_revision = "f5c962df755c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "automatic_picking_task_completions",
        sa.Column("first_plan_due_at", sa.DateTime(), nullable=True),
        schema="wes_biz",
    )
    op.alter_column("automatic_picking_task_completions", "confirmation_id", nullable=True, schema="wes_biz")
    op.create_check_constraint(
        op.f("ck_automatic_picking_task_completions_automatic_completion_has_owner"),
        "automatic_picking_task_completions",
        "confirmation_id IS NOT NULL OR first_plan_due_at IS NOT NULL",
        schema="wes_biz",
    )


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM wes_biz.automatic_picking_task_completions "
                "WHERE confirmation_id IS NULL OR first_plan_due_at IS NOT NULL)"
            )
        )
        .scalar()
    ):
        raise RuntimeError("cannot discard pending first-plan deadline")
    op.drop_constraint(
        op.f("ck_automatic_picking_task_completions_automatic_completion_has_owner"),
        "automatic_picking_task_completions",
        type_="check",
        schema="wes_biz",
    )
    op.alter_column("automatic_picking_task_completions", "confirmation_id", nullable=False, schema="wes_biz")
    op.drop_column("automatic_picking_task_completions", "first_plan_due_at", schema="wes_biz")
