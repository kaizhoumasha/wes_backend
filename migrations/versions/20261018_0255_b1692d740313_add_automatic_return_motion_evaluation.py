"""保存自动回架运动前求值的当前阶段与出口。

Revision ID: b1692d740313
Revises: a448148f9636
"""

import sqlalchemy as sa
from alembic import op

revision = "b1692d740313"
down_revision = "a448148f9636"
branch_labels = None
depends_on = None


def upgrade():
    columns = [
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
        sa.Column("arrival_evidence_id", sa.BigInteger(), nullable=False),
        sa.Column("source_evidence_id", sa.BigInteger(), nullable=False),
        sa.Column("rack_id", sa.String(100), nullable=False),
        sa.Column("rack_face", sa.String(10), nullable=False),
        sa.Column("allocation_id", sa.BigInteger(), nullable=True),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_automatic_picking_return_motions")),
        sa.UniqueConstraint(
            "workline_id", "arrival_evidence_id", "rack_id", "rack_face", name="ux_automatic_return_motion_face"
        ),
    ]
    for column, table in (
        ("workline_id", "work_lines"),
        ("arrival_evidence_id", "inbound_evidences"),
        ("source_evidence_id", "inbound_evidences"),
    ):
        columns.append(
            sa.ForeignKeyConstraint(
                [column], [f"wes_biz.{table}.id"], name=op.f(f"fk_automatic_picking_return_motions_{column}_{table}")
            )
        )
    op.create_table("automatic_picking_return_motions", *columns, schema="wes_biz")


def downgrade():
    if (
        op.get_bind()
        .execute(sa.text("SELECT EXISTS (SELECT 1 FROM wes_biz.automatic_picking_return_motions)"))
        .scalar()
    ):
        raise RuntimeError("cannot discard automatic motion evaluation identities or exit facts")
    op.drop_table("automatic_picking_return_motions", schema="wes_biz")
