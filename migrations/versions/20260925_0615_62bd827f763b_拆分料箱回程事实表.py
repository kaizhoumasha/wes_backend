"""拆分料箱回程事实表

Revision ID: 62bd827f763b
Revises: b6b5d9240f51
Create Date: 2026-09-25 06:15:31.014355+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "62bd827f763b"
down_revision: Union[str, Sequence[str], None] = "b6b5d9240f51"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """清线后启用新 Return execution；旧 Passage 回程列不回填。"""
    op.create_table(
        "manual_picking_inbound_batches",
        sa.Column("version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), autoincrement=True, nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("workline_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("picking_task_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("operation_id", sa.String(length=160), nullable=False),
        sa.Column("task_id", sa.String(length=100), nullable=False),
        sa.Column("plan_revision", sa.Integer(), nullable=False),
        sa.Column("rack_id", sa.String(length=100), nullable=False),
        sa.Column("rack_face", sa.String(length=100), nullable=False),
        sa.Column("response_evidence_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.ForeignKeyConstraint(["workline_id"], ["wes_biz.work_lines.id"]),
        sa.ForeignKeyConstraint(["response_evidence_id"], ["wes_biz.inbound_evidences.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_id", name="ux_manual_picking_inbound_batches_operation"),
        sa.UniqueConstraint(
            "workline_id", "task_id", "plan_revision", "rack_id", "rack_face",
            name="ux_manual_picking_inbound_batches_face",
        ),
        schema="wes_biz",
    )
    op.create_table(
        "manual_picking_inbound_batch_scans",
        sa.Column("version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), autoincrement=True, nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("workline_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("operation_id", sa.String(length=160), nullable=False),
        sa.Column("bin_code", sa.String(length=100), nullable=False),
        sa.ForeignKeyConstraint(["workline_id"], ["wes_biz.work_lines.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_id", "bin_code", name="ux_manual_picking_inbound_batch_scans_member"),
        schema="wes_biz",
    )
    op.create_table(
        "bin_line_returns",
        sa.Column("version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), autoincrement=True, nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("workline_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("bin_code", sa.String(length=100), nullable=False),
        sa.Column("scan3_evidence_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("scan4_evidence_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("scan4_event_time", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("return_batch_evidence_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("scan3_command_code", sa.String(length=160), nullable=True),
        sa.Column("scan4_command_code", sa.String(length=160), nullable=True),
        sa.Column("return_state", sa.String(length=20), nullable=False),
        sa.CheckConstraint(
            "return_state IN ('NONE', 'MOVE_PENDING', 'READY', 'RETURN_REQUESTED', 'EXITED', 'VOIDED')",
            name="bin_line_returns_state_valid",
        ),
        sa.CheckConstraint(
            "(scan4_evidence_id IS NULL) = (scan4_event_time IS NULL)",
            name="bin_line_returns_scan4_order_complete",
        ),
        sa.ForeignKeyConstraint(["workline_id"], ["wes_biz.work_lines.id"]),
        sa.ForeignKeyConstraint(["scan3_evidence_id"], ["wes_biz.inbound_evidences.id"]),
        sa.ForeignKeyConstraint(["scan4_evidence_id"], ["wes_biz.inbound_evidences.id"]),
        sa.ForeignKeyConstraint(["return_batch_evidence_id"], ["wes_biz.inbound_evidences.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scan3_evidence_id", name="ux_bin_line_returns_scan3_evidence"),
        sa.UniqueConstraint("scan4_evidence_id", name="ux_bin_line_returns_scan4_evidence"),
        schema="wes_biz",
    )
    op.create_index(
        "ux_bin_line_returns_current_bin",
        "bin_line_returns",
        ["workline_id", "bin_code"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("return_state NOT IN ('EXITED', 'VOIDED')"),
    )
    op.create_index(
        "ix_bin_line_returns_fifo",
        "bin_line_returns",
        ["workline_id", "scan4_event_time", "scan4_evidence_id"],
        schema="wes_biz",
        postgresql_where=sa.text("scan4_evidence_id IS NOT NULL AND return_state NOT IN ('EXITED', 'VOIDED')"),
    )
    op.drop_index("ix_manual_picking_passages_return_fifo", table_name="manual_picking_passages", schema="wes_biz")
    op.drop_constraint(
        op.f("ck_manual_picking_passages_manual_picking_return_state_valid"),
        "manual_picking_passages",
        type_="check",
        schema="wes_biz",
    )
    op.drop_constraint(
        op.f("ck_manual_picking_passages_manual_picking_scan4_order_complete"),
        "manual_picking_passages",
        type_="check",
        schema="wes_biz",
    )
    op.drop_constraint(
        "ux_manual_picking_passages_scan4_evidence",
        "manual_picking_passages",
        type_="unique",
        schema="wes_biz",
    )
    for column in (
        "scan3_command_code",
        "scan3_route",
        "scan4_evidence_id",
        "scan4_received_at",
        "scan4_command_code",
        "return_state",
    ):
        op.drop_column("manual_picking_passages", column, schema="wes_biz")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM wes_biz.bin_line_returns) "
        "THEN RAISE EXCEPTION 'bin_line_returns is not empty'; END IF; END $$;"
    )
    for column in (
        sa.Column("scan3_command_code", sa.String(length=160), nullable=True),
        sa.Column("scan3_route", sa.String(length=20), nullable=True),
        sa.Column("scan4_evidence_id", sa.BigInteger(), nullable=True),
        sa.Column("scan4_received_at", sa.DateTime(), nullable=True),
        sa.Column("scan4_command_code", sa.String(length=160), nullable=True),
        sa.Column("return_state", sa.String(length=20), server_default="NONE", nullable=False),
    ):
        op.add_column("manual_picking_passages", column, schema="wes_biz")
    op.create_foreign_key(
        None,
        "manual_picking_passages",
        "inbound_evidences",
        ["scan4_evidence_id"],
        ["id"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )
    op.create_unique_constraint(
        "ux_manual_picking_passages_scan4_evidence", "manual_picking_passages", ["scan4_evidence_id"], schema="wes_biz"
    )
    op.create_check_constraint(
        op.f("ck_manual_picking_passages_manual_picking_return_state_valid"),
        "manual_picking_passages",
        "return_state IN ('NONE', 'MOVE_PENDING', 'READY', 'RETURN_REQUESTED', 'RETURNED')",
        schema="wes_biz",
    )
    op.create_check_constraint(
        op.f("ck_manual_picking_passages_manual_picking_scan4_order_complete"),
        "manual_picking_passages",
        "(scan4_evidence_id IS NULL) = (scan4_received_at IS NULL)",
        schema="wes_biz",
    )
    op.create_index(
        "ix_manual_picking_passages_return_fifo",
        "manual_picking_passages",
        ["workline_id", "scan4_received_at", "scan4_evidence_id"],
        schema="wes_biz",
        postgresql_where=sa.text("scan4_evidence_id IS NOT NULL AND return_state <> 'RETURNED'"),
    )
    op.drop_index("ix_bin_line_returns_fifo", table_name="bin_line_returns", schema="wes_biz")
    op.drop_index("ux_bin_line_returns_current_bin", table_name="bin_line_returns", schema="wes_biz")
    op.drop_table("bin_line_returns", schema="wes_biz")
    op.drop_table("manual_picking_inbound_batch_scans", schema="wes_biz")
    op.drop_table("manual_picking_inbound_batches", schema="wes_biz")
