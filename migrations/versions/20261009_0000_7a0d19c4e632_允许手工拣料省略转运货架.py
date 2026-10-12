"""允许手工拣料省略转运货架。

Revision ID: 7a0d19c4e632
Revises: bdf2d676d0a8
"""

from alembic import op

revision = "7a0d19c4e632"
down_revision = "bdf2d676d0a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("picking_task_plan_initial_consistent", "picking_tasks", schema="wes_biz", type_="check")
    op.create_check_constraint(
        "picking_task_plan_initial_consistent",
        "picking_tasks",
        "(last_applied_plan_revision = 0 AND target_rack_id IS NULL AND target_rack_face IS NULL "
        "AND initial_plan_evidence_id IS NULL AND last_plan_evidence_id IS NULL) OR "
        "(last_applied_plan_revision > 0 AND "
        "((target_rack_id IS NOT NULL AND target_rack_face IS NOT NULL) OR "
        "(task_type = 'MANUAL' AND target_rack_id IS NULL AND target_rack_face IS NULL)) "
        "AND initial_plan_evidence_id IS NOT NULL AND last_plan_evidence_id IS NOT NULL)",
        schema="wes_biz",
    )


def downgrade() -> None:
    # 恢复约束会拒绝已有无目标架任务；不得为降级补造目标或删除业务数据。
    op.drop_constraint("picking_task_plan_initial_consistent", "picking_tasks", schema="wes_biz", type_="check")
    op.create_check_constraint(
        "picking_task_plan_initial_consistent",
        "picking_tasks",
        "(last_applied_plan_revision = 0 AND target_rack_id IS NULL AND target_rack_face IS NULL "
        "AND initial_plan_evidence_id IS NULL AND last_plan_evidence_id IS NULL) OR "
        "(last_applied_plan_revision > 0 AND target_rack_id IS NOT NULL AND target_rack_face IS NOT NULL "
        "AND initial_plan_evidence_id IS NOT NULL AND last_plan_evidence_id IS NOT NULL)",
        schema="wes_biz",
    )
