"""extend workline inbox retry status

Revision ID: 3b7c9d2e4f11
Revises: f4cd014e337e
Create Date: 2026-04-13 18:15:00+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3b7c9d2e4f11"
down_revision: Union[str, Sequence[str], None] = "f4cd014e337e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _recreate_inbox_status_constraint(*, include_retry_statuses: bool) -> None:
    allowed_statuses = [
        "NEW",
        "PROCESSING",
        "PROCESSED",
        "FAILED",
    ]
    if include_retry_statuses:
        allowed_statuses.extend(["RETRY", "DEAD_LETTER"])

    allowed_statuses_sql = ",\n                ".join(f"'{status}'" for status in allowed_statuses)

    op.execute(
        """
        ALTER TABLE wes_biz.workline_inbox
        DROP CONSTRAINT IF EXISTS inboxstatus
        """
    )
    op.execute(
        """
        ALTER TABLE wes_biz.workline_inbox
        DROP CONSTRAINT IF EXISTS ck_workline_inbox_inboxstatus
        """
    )
    op.execute(
        f"""
        ALTER TABLE wes_biz.workline_inbox
        ADD CONSTRAINT ck_workline_inbox_inboxstatus
        CHECK (
            status IN (
                {allowed_statuses_sql}
            )
        )
        """
    )


def upgrade() -> None:
    """Upgrade schema."""

    op.add_column(
        "workline_inbox",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        schema="wes_biz",
    )
    op.add_column(
        "workline_inbox",
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        schema="wes_biz",
    )
    op.add_column(
        "workline_inbox",
        sa.Column("next_retry_at", sa.DateTime(), nullable=True),
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_workline_inbox_next_retry_at"),
        "workline_inbox",
        ["next_retry_at"],
        unique=False,
        schema="wes_biz",
    )
    op.execute(
        """
        UPDATE wes_biz.workline_inbox
        SET attempt_count = 0
        WHERE attempt_count IS NULL
        """
    )
    op.execute(
        """
        UPDATE wes_biz.workline_inbox
        SET max_attempts = 3
        WHERE max_attempts IS NULL
        """
    )
    op.alter_column("workline_inbox", "attempt_count", server_default=None, schema="wes_biz")
    op.alter_column("workline_inbox", "max_attempts", server_default=None, schema="wes_biz")
    _recreate_inbox_status_constraint(include_retry_statuses=True)


def downgrade() -> None:
    """Downgrade schema."""

    op.execute(
        """
        UPDATE wes_biz.workline_inbox
        SET status = 'FAILED'
        WHERE status IN ('RETRY', 'DEAD_LETTER')
        """
    )
    _recreate_inbox_status_constraint(include_retry_statuses=False)
    op.drop_index(op.f("ix_wes_biz_workline_inbox_next_retry_at"), table_name="workline_inbox", schema="wes_biz")
    op.drop_column("workline_inbox", "next_retry_at", schema="wes_biz")
    op.drop_column("workline_inbox", "max_attempts", schema="wes_biz")
    op.drop_column("workline_inbox", "attempt_count", schema="wes_biz")
