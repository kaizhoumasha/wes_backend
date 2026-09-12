"""retire device event command blocks

Revision ID: 6cf85c1760e4
Revises: db9bf1bdb493
Create Date: 2026-09-12 23:28:48.474574+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6cf85c1760e4"
down_revision: Union[str, Sequence[str], None] = "db9bf1bdb493"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """删除已退役的 EVENT 命令 blocker 辅助表。"""
    op.drop_table("device_event_command_blocks", schema="wes_biz")


def downgrade() -> None:
    """只恢复旧结构，不伪造已删除的 blocker 历史。"""
    op.create_table(
        "device_event_command_blocks",
        sa.Column("version", sa.Integer(), server_default="0", nullable=False, comment="版本号"),
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间 (UTC)"),
        sa.Column("updated_at", sa.DateTime(), nullable=True, comment="更新时间 (UTC)"),
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
            comment="主键 ID",
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人ID"),
        sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人ID"),
        sa.Column("evidence_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("source_event_id", sa.String(length=300), nullable=False),
        sa.Column("device_code", sa.String(length=100), nullable=False),
        sa.Column("blocking_command_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("blocking_command_code", sa.String(length=100), nullable=False),
        sa.Column(
            "blocking_command_status",
            sa.Enum(
                "PENDING",
                "DISPATCHING",
                "ACKNOWLEDGED",
                "RECONCILING",
                "SUCCEEDED",
                "FAILED",
                "TIMED_OUT",
                name="commandstatus",
                native_enum=False,
                create_constraint=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("blocking_reconciliation_reason", sa.String(length=120), nullable=True),
        sa.Column("reason_code", sa.String(length=120), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "BLOCKED",
                "REQUEUED",
                name="deviceeventcommandblockstatus",
                native_enum=False,
                create_constraint=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("blocked_at", sa.DateTime(), nullable=False),
        sa.Column("requeued_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "((status = 'BLOCKED' AND requeued_at IS NULL) OR (status = 'REQUEUED' AND requeued_at IS NOT NULL))",
            name=op.f("ck_device_event_command_blocks_device_event_command_block_status_time_complete"),
        ),
        sa.CheckConstraint(
            "blocking_command_status IN ('PENDING', 'DISPATCHING', 'ACKNOWLEDGED', 'RECONCILING')",
            name=op.f("ck_device_event_command_blocks_device_event_command_block_command_status_valid"),
        ),
        sa.CheckConstraint(
            "reason_code = 'DEVICE_HAS_ACTIVE_COMMAND'",
            name=op.f("ck_device_event_command_blocks_device_event_command_block_reason_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('BLOCKED', 'REQUEUED')",
            name=op.f("ck_device_event_command_blocks_device_event_command_block_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["blocking_command_id"],
            ["wes_biz.device_commands.id"],
            name=op.f("fk_device_event_command_blocks_blocking_command_id_device_commands"),
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["wes_biz.inbound_evidences.id"],
            name=op.f("fk_device_event_command_blocks_evidence_id_inbound_evidences"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_device_event_command_blocks")),
        schema="wes_biz",
    )
    op.create_index(
        "ix_device_event_command_blocks_evidence_history",
        "device_event_command_blocks",
        ["evidence_id", "blocked_at", "id"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        "ux_device_event_command_blocks_open_evidence",
        "device_event_command_blocks",
        ["evidence_id"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("status = 'BLOCKED'"),
        sqlite_where=sa.text("status = 'BLOCKED'"),
    )
