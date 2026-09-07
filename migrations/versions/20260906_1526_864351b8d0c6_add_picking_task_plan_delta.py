"""add_picking_task_plan_delta

Revision ID: 864351b8d0c6
Revises: 627291489210
Create Date: 2026-09-06 15:26:08.611204+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "864351b8d0c6"
down_revision: Union[str, Sequence[str], None] = "627291489210"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # pgcrypto 的 text digest 为 IMMUTABLE；不对无界 opaque face 建原文 B-tree 键。
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public")
    op.add_column(
        "picking_tasks",
        sa.Column("last_applied_plan_revision", sa.BigInteger(), server_default="0", nullable=False),
        schema="wes_biz",
    )
    op.add_column("picking_tasks", sa.Column("target_rack_id", sa.String(100)), schema="wes_biz")
    op.add_column("picking_tasks", sa.Column("target_rack_face", sa.Text()), schema="wes_biz")
    for column in ("initial_plan_evidence_id", "last_plan_evidence_id", "plan_blocked_evidence_id"):
        op.add_column("picking_tasks", sa.Column(column, sa.BigInteger()), schema="wes_biz")
        op.create_foreign_key(
            f"fk_picking_tasks_{column}_inbound_evidences",
            "picking_tasks",
            "inbound_evidences",
            [column],
            ["id"],
            source_schema="wes_biz",
            referent_schema="wes_biz",
        )
    op.create_check_constraint(
        "picking_task_plan_revision_nonnegative", "picking_tasks", "last_applied_plan_revision >= 0", schema="wes_biz"
    )
    op.create_check_constraint(
        "picking_task_plan_initial_consistent",
        "picking_tasks",
        "(last_applied_plan_revision = 0 AND target_rack_id IS NULL AND target_rack_face IS NULL AND initial_plan_evidence_id IS NULL AND last_plan_evidence_id IS NULL) OR (last_applied_plan_revision > 0 AND target_rack_id IS NOT NULL AND target_rack_face IS NOT NULL AND initial_plan_evidence_id IS NOT NULL AND last_plan_evidence_id IS NOT NULL)",
        schema="wes_biz",
    )
    for table, prefix, unique in (
        ("direct_pick_executions", "direct_pick", "ux_direct_pick_source"),
        ("picking_task_bin_source_racks", "picking_bin_source", "ux_picking_bin_source"),
    ):
        columns = [
            sa.Column("version", sa.Integer(), server_default="0", nullable=False, comment="版本号"),
            sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间 (UTC)"),
            sa.Column("updated_at", sa.DateTime(), nullable=True, comment="更新时间 (UTC)"),
            sa.Column(
                "id",
                sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                primary_key=True,
                autoincrement=True,
                comment="主键 ID",
            ),
            sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人ID"),
            sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人ID"),
            sa.Column("picking_task_id", sa.BigInteger(), sa.ForeignKey("wes_biz.picking_tasks.id"), nullable=False),
            sa.Column("rack_id", sa.String(100), nullable=False),
            sa.Column("rack_face", sa.Text(), nullable=False),
            sa.Column("plan_revision", sa.BigInteger(), nullable=False),
            sa.Column(
                "source_evidence_id", sa.BigInteger(), sa.ForeignKey("wes_biz.inbound_evidences.id"), nullable=False
            ),
        ]
        identity = ["picking_task_id", "rack_id", sa.text("public.digest(rack_face, 'sha256')")]
        if table == "direct_pick_executions":
            columns.append(sa.Column("slot_id", sa.String(100), nullable=False))
            identity.append("slot_id")
        op.create_table(
            table,
            *columns,
            sa.CheckConstraint("plan_revision >= 1", name=f"{prefix}_revision_positive"),
            sa.CheckConstraint("length(rack_face) > 0", name=f"{prefix}_face_nonempty"),
            schema="wes_biz",
        )
        op.create_index(unique, table, identity, unique=True, schema="wes_biz")


def downgrade() -> None:
    op.drop_table("picking_task_bin_source_racks", schema="wes_biz")
    op.drop_table("direct_pick_executions", schema="wes_biz")
    for constraint in ("picking_task_plan_initial_consistent", "picking_task_plan_revision_nonnegative"):
        op.drop_constraint(op.f(f"ck_picking_tasks_{constraint}"), "picking_tasks", schema="wes_biz", type_="check")
    for column in (
        "plan_blocked_evidence_id",
        "last_plan_evidence_id",
        "initial_plan_evidence_id",
        "target_rack_face",
        "target_rack_id",
        "last_applied_plan_revision",
    ):
        op.drop_column("picking_tasks", column, schema="wes_biz")
