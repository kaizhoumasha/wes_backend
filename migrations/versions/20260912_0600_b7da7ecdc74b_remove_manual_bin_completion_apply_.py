"""remove manual bin completion apply report phase

Revision ID: b7da7ecdc74b
Revises: f7cf0cd8c6d4
Create Date: 2026-09-12 06:00:52.408869+08:00

"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7da7ecdc74b"
down_revision: Union[str, Sequence[str], None] = "f7cf0cd8c6d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_PHASES = (
    "'BIND_TASK','TASK_PREPARE','PLAN_RECEIPT','RACK_TRANSPORT','RACK_ARRIVAL',"
    "'BIN_INBOUND_BATCH','BIN_TRANSPORT','POINT1_ARRIVAL','POINT2_SCAN','WORK_ADMISSION',"
    "'WORK_COMPLETION','POINT2_RELEASE','POINT3_ROUTE','RETURN_BUFFER','BIN_RETURN_BATCH',"
    "'BIN_RETURN_TRANSPORT','RACK_DEPARTURE','TASK_COMPLETION','CLEANUP'"
)
_CONSTRAINT = "ck_workline_integration_runs_workline_integration_run_phase_valid"


def upgrade() -> None:
    # 不把接口退役当作未知可靠义务已闭合，也不伪造旧 Run 的完成证据。
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM wes_runtime.workline_integration_runs
                     WHERE current_phase = 'COMPLETION_REPORT') THEN
            RAISE EXCEPTION 'Resolve COMPLETION_REPORT runs before retiring the operation';
          END IF;
          IF EXISTS (SELECT 1 FROM wes_biz.wms_confirmations
                     WHERE operation = 'outbound.manual_bin.completion_apply_report@v1'
                       AND status <> 'COMPLETED') THEN
            RAISE EXCEPTION 'Resolve outstanding completion_apply_report obligations before retirement';
          END IF;
        END $$;
    """)
    op.drop_constraint(op.f(_CONSTRAINT), "workline_integration_runs", schema="wes_runtime", type_="check")
    op.create_check_constraint(
        op.f(_CONSTRAINT), "workline_integration_runs", f"current_phase IN ({_PHASES})", schema="wes_runtime"
    )


def downgrade() -> None:
    op.drop_constraint(op.f(_CONSTRAINT), "workline_integration_runs", schema="wes_runtime", type_="check")
    op.create_check_constraint(
        op.f(_CONSTRAINT),
        "workline_integration_runs",
        f"current_phase IN ({_PHASES},'COMPLETION_REPORT')",
        schema="wes_runtime",
    )
