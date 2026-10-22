"""新增人工任务业务类型；既有任务均为出库。"""

import sqlalchemy as sa
from alembic import op

revision = "a8c5e2107b94"
down_revision = "f72bd038e915"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "picking_tasks",
        sa.Column("business_type", sa.String(8), nullable=False, server_default="outbound"),
        schema="wes_biz",
    )
    op.create_check_constraint(
        "picking_task_business_type_valid",
        "picking_tasks",
        "business_type IN ('inbound', 'outbound')",
        schema="wes_biz",
    )
    op.create_check_constraint(
        "picking_task_auto_outbound",
        "picking_tasks",
        "task_type = 'MANUAL' OR business_type = 'outbound'",
        schema="wes_biz",
    )


def downgrade():
    op.drop_constraint("picking_task_auto_outbound", "picking_tasks", schema="wes_biz", type_="check")
    op.drop_constraint("picking_task_business_type_valid", "picking_tasks", schema="wes_biz", type_="check")
    op.drop_column("picking_tasks", "business_type", schema="wes_biz")
