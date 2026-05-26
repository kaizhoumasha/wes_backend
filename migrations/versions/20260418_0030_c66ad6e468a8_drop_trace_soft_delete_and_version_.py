"""drop trace soft delete and version columns

Revision ID: c66ad6e468a8
Revises: a7c4d5e6f7a8
Create Date: 2026-04-18 00:30:30.000283+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c66ad6e468a8"
down_revision: Union[str, Sequence[str], None] = "a7c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # `workline_timelines` / `workline_inbox` 当前实际 schema
    # 已不存在本轮要清理的列，因此 migration 仅处理真实残留的三张表。
    op.drop_column("callback_logs", "deleted_by", schema="wes_biz")
    op.drop_column("callback_logs", "is_deleted", schema="wes_biz")
    op.drop_column("callback_logs", "deleted_at", schema="wes_biz")

    op.drop_column("workline_sessions", "deleted_by", schema="wes_biz")
    op.drop_column("workline_sessions", "is_deleted", schema="wes_biz")
    op.drop_column("workline_sessions", "deleted_at", schema="wes_biz")

    op.drop_column("device_commands", "deleted_by", schema="wes_biz")
    op.drop_column("device_commands", "version", schema="wes_biz")
    op.drop_column("device_commands", "is_deleted", schema="wes_biz")
    op.drop_column("device_commands", "deleted_at", schema="wes_biz")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        "device_commands",
        sa.Column("deleted_at", postgresql.TIMESTAMP(), nullable=True, comment="删除时间"),
        schema="wes_biz",
    )
    op.add_column(
        "device_commands",
        sa.Column(
            "is_deleted",
            sa.BOOLEAN(),
            server_default=sa.text("false"),
            nullable=False,
            comment="是否已删除",
        ),
        schema="wes_biz",
    )
    op.add_column(
        "device_commands",
        sa.Column("version", sa.INTEGER(), server_default=sa.text("0"), nullable=False, comment="版本号"),
        schema="wes_biz",
    )
    op.add_column(
        "device_commands",
        sa.Column("deleted_by", sa.INTEGER(), nullable=True, comment="删除人ID"),
        schema="wes_biz",
    )

    op.add_column(
        "workline_sessions",
        sa.Column("deleted_at", postgresql.TIMESTAMP(), nullable=True, comment="删除时间"),
        schema="wes_biz",
    )
    op.add_column(
        "workline_sessions",
        sa.Column(
            "is_deleted",
            sa.BOOLEAN(),
            server_default=sa.text("false"),
            nullable=False,
            comment="是否已删除",
        ),
        schema="wes_biz",
    )
    op.add_column(
        "workline_sessions",
        sa.Column("deleted_by", sa.INTEGER(), nullable=True, comment="删除人ID"),
        schema="wes_biz",
    )

    op.add_column(
        "callback_logs",
        sa.Column("deleted_at", postgresql.TIMESTAMP(), nullable=True, comment="删除时间"),
        schema="wes_biz",
    )
    op.add_column(
        "callback_logs",
        sa.Column(
            "is_deleted",
            sa.BOOLEAN(),
            server_default=sa.text("false"),
            nullable=False,
            comment="是否已删除",
        ),
        schema="wes_biz",
    )
    op.add_column(
        "callback_logs",
        sa.Column("deleted_by", sa.INTEGER(), nullable=True, comment="删除人ID"),
        schema="wes_biz",
    )
