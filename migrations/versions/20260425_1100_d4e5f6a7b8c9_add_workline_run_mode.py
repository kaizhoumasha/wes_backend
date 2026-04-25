"""add workline run mode

Revision ID: d4e5f6a7b8c9
Revises: c66ad6e468a8
Create Date: 2026-04-25 11:00:00.000000+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c66ad6e468a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "work_lines",
        sa.Column("run_mode", sa.String(length=50), server_default="AUTO", nullable=False),
        schema="wes_biz",
    )
    op.create_check_constraint(
        "ck_work_lines_run_mode",
        "work_lines",
        "run_mode IN ('AUTO', 'MANUAL', 'SIMULATION')",
        schema="wes_biz",
    )
    op.create_index(op.f("ix_wes_biz_work_lines_run_mode"), "work_lines", ["run_mode"], unique=False, schema="wes_biz")

    # REPLAY 不再属于 WorklineSession runtime mode；如需 payload 诊断，后续放在插件诊断工具中。
    op.execute(sa.text("UPDATE wes_biz.workline_sessions SET run_mode = 'AUTO' WHERE run_mode = 'REPLAY'"))
    op.drop_constraint("runmode", "workline_sessions", schema="wes_biz", type_="check")
    op.create_check_constraint(
        "ck_workline_sessions_run_mode",
        "workline_sessions",
        "run_mode IN ('AUTO', 'MANUAL', 'SIMULATION')",
        schema="wes_biz",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("ck_workline_sessions_run_mode", "workline_sessions", schema="wes_biz", type_="check")
    op.create_check_constraint(
        "runmode",
        "workline_sessions",
        "run_mode IN ('AUTO', 'MANUAL', 'SIMULATION', 'REPLAY')",
        schema="wes_biz",
    )

    op.drop_index(op.f("ix_wes_biz_work_lines_run_mode"), table_name="work_lines", schema="wes_biz")
    op.drop_constraint("ck_work_lines_run_mode", "work_lines", schema="wes_biz", type_="check")
    op.drop_column("work_lines", "run_mode", schema="wes_biz")
