"""支持排空人工撤出；保留真实搬运终态和全部证据。"""

import sqlalchemy as sa
from alembic import op

revision = "f6d0319c8a42"
down_revision = "e3fec1d9ffae"
branch_labels = None
depends_on = None

_OLD = "(status = 'ACTIVE' AND closed_at IS NULL AND reason_code IS NULL AND blocker_client_request_id IS NULL AND trigger_picking_task_id IS NULL) OR (status = 'COMPLETED' AND closed_at IS NOT NULL AND reason_code IS NULL AND blocker_client_request_id IS NULL AND trigger_picking_task_id IS NULL) OR (status = 'EXCEPTION_RETIRED' AND closed_at IS NOT NULL AND reason_code IS NOT NULL AND reason_code IN ('DRAIN_ACTION_TERMINAL_EXCEPTION', 'DRAIN_RESULT_OVERDUE', 'DRAIN_RESULT_UNKNOWN') AND blocker_client_request_id IS NOT NULL AND trigger_picking_task_id IS NOT NULL)"
_NEW = "(status = 'ACTIVE' AND closed_at IS NULL AND reason_code IS NULL AND blocker_client_request_id IS NULL AND trigger_picking_task_id IS NULL) OR (status = 'COMPLETED' AND closed_at IS NOT NULL AND reason_code IS NULL AND blocker_client_request_id IS NULL AND trigger_picking_task_id IS NULL) OR (status = 'EXCEPTION_RETIRED' AND closed_at IS NOT NULL AND reason_code IS NOT NULL AND reason_code IN ('DRAIN_ACTION_TERMINAL_EXCEPTION', 'DRAIN_RESULT_OVERDUE', 'DRAIN_RESULT_UNKNOWN') AND blocker_client_request_id IS NOT NULL AND trigger_picking_task_id IS NOT NULL) OR (status = 'MANUAL_WITHDRAWN' AND closed_at IS NOT NULL AND reason_code IS NOT NULL AND reason_code = 'MANUAL_SCOPE_EXIT' AND blocker_client_request_id IS NOT NULL AND trigger_picking_task_id IS NOT NULL)"
_NAME = "manual_picking_drain_execution_lifecycle_valid"
_TABLE = "manual_picking_drain_executions"
_SCHEMA = "wes_biz"


def upgrade():
    op.drop_constraint(_NAME, _TABLE, type_="check", schema=_SCHEMA)
    op.create_check_constraint(_NAME, _TABLE, _NEW, schema=_SCHEMA)


def downgrade():
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS(SELECT 1 FROM wes_biz.manual_picking_drain_executions WHERE status='MANUAL_WITHDRAWN')"
            )
        )
        .scalar()
    ):
        raise RuntimeError("manual drain withdrawals exist; downgrade would discard their business state")
    op.drop_constraint(_NAME, _TABLE, type_="check", schema=_SCHEMA)
    op.create_check_constraint(_NAME, _TABLE, _OLD, schema=_SCHEMA)
