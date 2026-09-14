"""添加人工拣料经过状态

Revision ID: e5c5dfb4373f
Revises: 9d275378a869
Create Date: 2026-09-13 23:38:06.542543+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5c5dfb4373f"
down_revision: Union[str, Sequence[str], None] = "9d275378a869"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "manual_picking_passages",
        sa.Column("version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), autoincrement=True, nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("workline_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("task_id", sa.String(length=100), nullable=False),
        sa.Column("bin_code", sa.String(length=100), nullable=True),
        sa.Column("raw_scan1_code", sa.String(length=160), nullable=True),
        sa.Column("scan1_evidence_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("scan1_received_at", sa.DateTime(), nullable=False),
        sa.Column("scan2_evidence_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("scan2_fault_evidence_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("scan3_evidence_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("scan4_evidence_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("scan4_received_at", sa.DateTime(), nullable=True),
        sa.Column("disposition", sa.String(length=10), nullable=False),
        sa.Column("admission_operation_id", sa.String(length=160), nullable=True),
        sa.Column("admission_result", sa.String(length=20), nullable=True),
        sa.Column("admission_scanned_at", sa.BigInteger(), nullable=True),
        sa.Column("wms_result", sa.String(length=10), nullable=True),
        sa.Column("wms_completed_at", sa.DateTime(), nullable=True),
        sa.Column("wms_completed_evidence_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("scan1_command_code", sa.String(length=160), nullable=True),
        sa.Column("scan2_command_code", sa.String(length=160), nullable=True),
        sa.Column("scan2_fault_command_code", sa.String(length=160), nullable=True),
        sa.Column("scan3_command_code", sa.String(length=160), nullable=True),
        sa.Column("scan3_route", sa.String(length=20), nullable=True),
        sa.Column("scan4_command_code", sa.String(length=160), nullable=True),
        sa.Column("return_state", sa.String(length=20), nullable=False),
        sa.CheckConstraint(
            "disposition IN ('OPEN', 'NORMAL', 'NG', 'CLOSED')", name="manual_picking_disposition_valid"
        ),
        sa.CheckConstraint(
            "return_state IN ('NONE', 'MOVE_PENDING', 'READY', 'RETURN_REQUESTED', 'RETURNED')",
            name="manual_picking_return_state_valid",
        ),
        sa.CheckConstraint(
            "wms_result IS NULL OR wms_result IN ('NORMAL', 'NG')", name="manual_picking_wms_result_valid"
        ),
        sa.CheckConstraint(
            "(scan4_evidence_id IS NULL) = (scan4_received_at IS NULL)",
            name="manual_picking_scan4_order_complete",
        ),
        sa.ForeignKeyConstraint(["workline_id"], ["wes_biz.work_lines.id"]),
        sa.ForeignKeyConstraint(["scan1_evidence_id"], ["wes_biz.inbound_evidences.id"]),
        sa.ForeignKeyConstraint(["scan2_evidence_id"], ["wes_biz.inbound_evidences.id"]),
        sa.ForeignKeyConstraint(["scan2_fault_evidence_id"], ["wes_biz.inbound_evidences.id"]),
        sa.ForeignKeyConstraint(["scan3_evidence_id"], ["wes_biz.inbound_evidences.id"]),
        sa.ForeignKeyConstraint(["scan4_evidence_id"], ["wes_biz.inbound_evidences.id"]),
        sa.ForeignKeyConstraint(["wms_completed_evidence_id"], ["wes_biz.inbound_evidences.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scan1_evidence_id", name="ux_manual_picking_passages_scan1_evidence"),
        sa.UniqueConstraint("scan2_evidence_id", name="ux_manual_picking_passages_scan2_evidence"),
        sa.UniqueConstraint("scan2_fault_evidence_id", name="ux_manual_picking_passages_scan2_fault_evidence"),
        sa.UniqueConstraint("scan3_evidence_id", name="ux_manual_picking_passages_scan3_evidence"),
        sa.UniqueConstraint("scan4_evidence_id", name="ux_manual_picking_passages_scan4_evidence"),
        sa.UniqueConstraint("wms_completed_evidence_id", name="ux_manual_picking_passages_wms_completed_evidence"),
        sa.UniqueConstraint("admission_operation_id", name="ux_manual_picking_passages_admission_operation"),
        schema="wes_biz",
    )
    op.create_index(
        "ix_manual_picking_passages_scan2_fifo",
        "manual_picking_passages",
        ["workline_id", "scan1_received_at", "scan1_evidence_id"],
        schema="wes_biz",
        postgresql_where=sa.text("scan2_evidence_id IS NULL AND disposition <> 'CLOSED'"),
    )
    op.create_index(
        "ix_manual_picking_passages_return_fifo",
        "manual_picking_passages",
        ["workline_id", "scan4_received_at", "scan4_evidence_id"],
        schema="wes_biz",
        postgresql_where=sa.text("scan4_evidence_id IS NOT NULL AND return_state <> 'RETURNED'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_manual_picking_passages_return_fifo", table_name="manual_picking_passages", schema="wes_biz")
    op.drop_index("ix_manual_picking_passages_scan2_fifo", table_name="manual_picking_passages", schema="wes_biz")
    op.drop_table("manual_picking_passages", schema="wes_biz")
