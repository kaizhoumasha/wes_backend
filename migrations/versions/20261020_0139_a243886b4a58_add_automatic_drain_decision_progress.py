"""add automatic drain decision progress

Revision ID: a243886b4a58
Revises: 6680af0fe85b
Create Date: 2026-10-20 01:39:42.071558+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a243886b4a58"
down_revision: Union[str, Sequence[str], None] = "6680af0fe85b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "automatic_picking_drains",
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
        sa.Column("workline_id", sa.BigInteger(), nullable=False),
        sa.Column("operation_id", sa.String(36), nullable=False),
        sa.Column("return_ids", sa.JSON(), nullable=False),
        sa.Column("response_evidence_id", sa.BigInteger(), nullable=True),
        sa.Column("retry_at", sa.DateTime(), nullable=True),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_automatic_picking_drains")),
        sa.UniqueConstraint("operation_id", name=op.f("uq_automatic_picking_drains_operation_id")),
        sa.ForeignKeyConstraint(
            ["workline_id"], ["wes_biz.work_lines.id"], name=op.f("fk_automatic_picking_drains_workline_id_work_lines")
        ),
        sa.ForeignKeyConstraint(
            ["response_evidence_id"],
            ["wes_biz.inbound_evidences.id"],
            name=op.f("fk_automatic_picking_drains_response_evidence_id_inbound_evidences"),
        ),
        sa.CheckConstraint(
            "retry_at IS NULL OR response_evidence_id IS NOT NULL",
            name=op.f("ck_automatic_picking_drains_automatic_drain_wait_basis"),
        ),
        schema="wes_biz",
    )
    op.create_index(
        "ix_automatic_drain_current",
        "automatic_picking_drains",
        ["workline_id"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("archived_at IS NULL"),
        sqlite_where=sa.text("archived_at IS NULL"),
    )


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM wes_biz.automatic_picking_drains)")).scalar():
        raise RuntimeError("cannot discard automatic drain current identities or reservations")
    op.drop_table("automatic_picking_drains", schema="wes_biz")
