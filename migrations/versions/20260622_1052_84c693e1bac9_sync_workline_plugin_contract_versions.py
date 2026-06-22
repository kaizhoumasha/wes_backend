"""sync workline plugin contract versions

Revision ID: 84c693e1bac9
Revises: e680301d30c8
Create Date: 2026-06-22 10:52:04.504496+08:00

"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "84c693e1bac9"
down_revision: Union[str, Sequence[str], None] = "e680301d30c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        UPDATE wes_biz.work_lines
        SET contract_version = 'rough_sorter.v2'
        WHERE plugin_key = 'rough_sorter'
          AND contract_version = 'rough_sorter.v1'
        """
    )
    op.execute(
        """
        UPDATE wes_biz.work_lines
        SET contract_version = '2026-06-21.p1'
        WHERE plugin_key = 'SMT_SORTING_INBOUND'
          AND contract_version = '2026-06-01.p0'
        """
    )
    op.execute(
        """
        UPDATE wes_biz.workline_sessions
        SET contract_version = 'rough_sorter.v2'
        WHERE plugin_key = 'rough_sorter'
          AND contract_version = 'rough_sorter.v1'
          AND status NOT IN ('COMPLETED', 'FAILED', 'CANCELLED')
        """
    )
    op.execute(
        """
        UPDATE wes_biz.workline_sessions
        SET contract_version = '2026-06-21.p1'
        WHERE plugin_key = 'SMT_SORTING_INBOUND'
          AND contract_version = '2026-06-01.p0'
          AND status NOT IN ('COMPLETED', 'FAILED', 'CANCELLED')
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        """
        UPDATE wes_biz.work_lines
        SET contract_version = 'rough_sorter.v1'
        WHERE plugin_key = 'rough_sorter'
          AND contract_version = 'rough_sorter.v2'
        """
    )
    op.execute(
        """
        UPDATE wes_biz.work_lines
        SET contract_version = '2026-06-01.p0'
        WHERE plugin_key = 'SMT_SORTING_INBOUND'
          AND contract_version = '2026-06-21.p1'
        """
    )
    op.execute(
        """
        UPDATE wes_biz.workline_sessions
        SET contract_version = 'rough_sorter.v1'
        WHERE plugin_key = 'rough_sorter'
          AND contract_version = 'rough_sorter.v2'
          AND status NOT IN ('COMPLETED', 'FAILED', 'CANCELLED')
        """
    )
    op.execute(
        """
        UPDATE wes_biz.workline_sessions
        SET contract_version = '2026-06-01.p0'
        WHERE plugin_key = 'SMT_SORTING_INBOUND'
          AND contract_version = '2026-06-21.p1'
          AND status NOT IN ('COMPLETED', 'FAILED', 'CANCELLED')
        """
    )
