"""add ecs_test run mode

Revision ID: 5bac3de5c2b5
Revises: b6b5d9240f51
Create Date: 2026-09-25 06:11:28.737557+08:00

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5bac3de5c2b5"
down_revision: Union[str, Sequence[str], None] = "b6b5d9240f51"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # metadata 的 naming_convention 会把这里的 name 当作 %(constraint_name)s 拼成
    # ck_work_lines_worklinerunmode，因此传未加前缀的短名，不要传拼好的全名。
    op.drop_constraint("worklinerunmode", "work_lines", schema="wes_biz", type_="check")
    op.create_check_constraint(
        "worklinerunmode",
        "work_lines",
        "run_mode IN ('AUTO', 'MANUAL', 'SIMULATION', 'ECS_TEST')",
        schema="wes_biz",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("worklinerunmode", "work_lines", schema="wes_biz", type_="check")
    op.create_check_constraint(
        "worklinerunmode",
        "work_lines",
        "run_mode IN ('AUTO', 'MANUAL', 'SIMULATION')",
        schema="wes_biz",
    )
