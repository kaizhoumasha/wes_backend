"""drop workline capacity and sort_order

Revision ID: b9d0e1f2a3b4
Revises: a8c9d0e1f2a3
Create Date: 2026-05-05 12:19:00.000000+08:00

capacity: 无任何业务逻辑消费，YAGNI。
sort_order: 手填数字排序是差设计，运行时查询改用 id 排序。
"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b9d0e1f2a3b4"
down_revision: Union[str, Sequence[str], None] = "a8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop capacity and sort_order from work_lines."""
    op.execute("ALTER TABLE wes_biz.work_lines DROP COLUMN IF EXISTS capacity")
    op.execute("ALTER TABLE wes_biz.work_lines DROP COLUMN IF EXISTS sort_order")


def downgrade() -> None:
    """Restore capacity and sort_order (data lost)."""
    op.execute("ALTER TABLE wes_biz.work_lines ADD COLUMN IF NOT EXISTS capacity INTEGER")
    op.execute("ALTER TABLE wes_biz.work_lines ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0")
