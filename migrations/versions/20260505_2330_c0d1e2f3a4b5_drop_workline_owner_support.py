"""drop workline owner and support fields

Revision ID: c0d1e2f3a4b5
Revises: b9d0e1f2a3b4
Create Date: 2026-05-05 23:30:00.000000+08:00

owner_team / support_contact 当前没有业务流消费，只是主数据占位。
WES 未发布，直接删除以避免过度设计扩散到运行态契约。
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c0d1e2f3a4b5"
down_revision: Union[str, Sequence[str], None] = "b9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop unused owner/support columns from work_lines."""

    op.execute("ALTER TABLE wes_biz.work_lines DROP COLUMN IF EXISTS owner_team")
    op.execute("ALTER TABLE wes_biz.work_lines DROP COLUMN IF EXISTS support_contact")


def downgrade() -> None:
    """Restore owner/support columns (data lost)."""

    op.add_column("work_lines", sa.Column("owner_team", sa.String(length=100), nullable=True), schema="wes_biz")
    op.add_column("work_lines", sa.Column("support_contact", sa.String(length=100), nullable=True), schema="wes_biz")
