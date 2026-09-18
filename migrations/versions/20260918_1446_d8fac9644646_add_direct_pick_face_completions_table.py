"""add direct_pick_face_completions table

Revision ID: d8fac9644646
Revises: 496bdbaaff26
Create Date: 2026-09-18 14:46:27.160071+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8fac9644646"
down_revision: Union[str, Sequence[str], None] = "496bdbaaff26"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "direct_pick_face_completions",
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
        sa.Column("rack_face", sa.String(10), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=False),
        sa.Column("source_evidence_id", sa.BigInteger(), sa.ForeignKey("wes_biz.inbound_evidences.id"), nullable=False),
        sa.CheckConstraint("length(rack_face) > 0", name="direct_pick_completion_face_nonempty"),
        schema="wes_biz",
    )
    op.create_index(
        "ux_direct_pick_face_completion",
        "direct_pick_face_completions",
        ["picking_task_id", "rack_id", "rack_face"],
        unique=True,
        schema="wes_biz",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("direct_pick_face_completions", schema="wes_biz")
