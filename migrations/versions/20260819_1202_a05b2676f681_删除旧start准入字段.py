"""删除旧START准入字段

Revision ID: a05b2676f681
Revises: 53e560430c1a
Create Date: 2026-08-19 12:02:58.687723+08:00

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a05b2676f681"
down_revision: Union[str, Sequence[str], None] = "53e560430c1a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Directly remove the retired START admission projection."""

    op.drop_column("work_lines", "last_start_trace_id", schema="wes_biz")
    op.drop_column("work_lines", "last_start_request_id", schema="wes_biz")
    op.drop_column("work_lines", "start_admission_checked_at", schema="wes_biz")
    op.drop_column("work_lines", "start_admission_failed_device_code", schema="wes_biz")
    op.drop_column("work_lines", "start_admission_message", schema="wes_biz")
    op.drop_column("work_lines", "start_admission_status", schema="wes_biz")


def downgrade() -> None:
    """Restore the nullable development-only projection columns."""

    op.add_column(
        "work_lines",
        sa.Column("start_admission_status", sa.String(length=50), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "work_lines",
        sa.Column("start_admission_message", sa.Text(), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "work_lines",
        sa.Column("start_admission_failed_device_code", sa.String(length=100), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "work_lines",
        sa.Column("start_admission_checked_at", sa.DateTime(), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "work_lines",
        sa.Column("last_start_request_id", sa.String(length=100), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "work_lines",
        sa.Column("last_start_trace_id", sa.String(length=100), nullable=True),
        schema="wes_biz",
    )
