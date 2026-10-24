"""新增人工来源架独立到位事实

Revision ID: e3fec1d9ffae
Revises: c6e209af714b
Create Date: 2026-10-23 03:35:02.745258+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e3fec1d9ffae"
down_revision: Union[str, Sequence[str], None] = "c6e209af714b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "manual_picking_source_arrivals",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("workline_id", sa.BigInteger(), nullable=False),
        sa.Column("picking_task_id", sa.BigInteger(), nullable=False),
        sa.Column("source_evidence_id", sa.BigInteger(), nullable=False),
        sa.Column("source_causal_token", sa.BigInteger(), nullable=False),
        sa.Column("source_transport_task_id", sa.String(80), nullable=False),
        sa.Column("plan_revision", sa.Integer(), nullable=False),
        sa.Column("rack_id", sa.String(100), nullable=False),
        sa.Column("rack_face", sa.String(10), nullable=False),
        sa.Column("location_code", sa.String(100), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("audit_id", sa.BigInteger(), nullable=False),
        sa.Column("confirmation_digest", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["workline_id"], ["wes_biz.work_lines.id"]),
        sa.ForeignKeyConstraint(["picking_task_id"], ["wes_biz.picking_tasks.id"]),
        sa.UniqueConstraint("workline_id", "source_transport_task_id", name="ux_manual_source_arrival_transport"),
        sa.CheckConstraint("source_causal_token > 0 AND plan_revision > 0", name="manual_source_arrival_positive"),
        schema="wes_biz",
    )

    op.create_index(
        "ux_manual_source_arrival_current",
        "manual_picking_source_arrivals",
        ["workline_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
        schema="wes_biz",
    )


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM wes_biz.manual_picking_source_arrivals)")).scalar():
        raise RuntimeError("manual source arrival records are not empty; downgrade would discard current facts")
    op.drop_table("manual_picking_source_arrivals", schema="wes_biz")
