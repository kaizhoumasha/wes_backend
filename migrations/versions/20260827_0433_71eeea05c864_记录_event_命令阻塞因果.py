"""记录 EVENT 命令阻塞因果

Revision ID: 71eeea05c864
Revises: 9624cc34fa93
Create Date: 2026-08-27 04:33:36.379586+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "71eeea05c864"
down_revision: Union[str, Sequence[str], None] = "9624cc34fa93"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """创建 EVENT 命令阻塞因果历史。"""

    op.create_table(
        "device_event_command_blocks",
        sa.Column("version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("evidence_id", sa.BigInteger(), nullable=False),
        sa.Column("source_event_id", sa.String(length=300), nullable=False),
        sa.Column("device_code", sa.String(length=100), nullable=False),
        sa.Column("blocking_command_id", sa.BigInteger(), nullable=False),
        sa.Column("blocking_command_code", sa.String(length=100), nullable=False),
        sa.Column("blocking_command_status", sa.String(length=20), nullable=False),
        sa.Column("blocking_reconciliation_reason", sa.String(length=120), nullable=True),
        sa.Column("reason_code", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("blocked_at", sa.DateTime(), nullable=False),
        sa.Column("requeued_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('BLOCKED', 'REQUEUED')",
            name="device_event_command_block_status_valid",
        ),
        sa.CheckConstraint(
            "reason_code = 'DEVICE_HAS_ACTIVE_COMMAND'",
            name="device_event_command_block_reason_valid",
        ),
        sa.CheckConstraint(
            "blocking_command_status IN ('PENDING', 'DISPATCHING', 'ACKNOWLEDGED', 'RECONCILING')",
            name="device_event_command_block_command_status_valid",
        ),
        sa.CheckConstraint(
            "((status = 'BLOCKED' AND requeued_at IS NULL) OR (status = 'REQUEUED' AND requeued_at IS NOT NULL))",
            name="device_event_command_block_status_time_complete",
        ),
        sa.ForeignKeyConstraint(["evidence_id"], ["wes_biz.inbound_evidences.id"]),
        sa.ForeignKeyConstraint(["blocking_command_id"], ["wes_biz.device_commands.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_device_event_command_blocks_id"),
        "device_event_command_blocks",
        ["id"],
        unique=True,
        schema="wes_biz",
    )
    op.create_index(
        "ix_device_event_command_blocks_evidence_history",
        "device_event_command_blocks",
        ["evidence_id", "blocked_at", "id"],
        schema="wes_biz",
    )
    op.create_index(
        "ux_device_event_command_blocks_open_evidence",
        "device_event_command_blocks",
        ["evidence_id"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("status = 'BLOCKED'"),
    )


def downgrade() -> None:
    """删除本 revision 新增的阻塞因果表。"""

    op.drop_table("device_event_command_blocks", schema="wes_biz")
