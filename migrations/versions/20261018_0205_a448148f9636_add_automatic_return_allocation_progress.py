"""保存自动回架的当前分配、成员与原搬运引用。

Revision ID: a448148f9636
Revises: 71246b611dbf
"""

import sqlalchemy as sa
from alembic import op

revision = "a448148f9636"
down_revision = "71246b611dbf"
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
        sa.Column("rack_id", sa.String(100), nullable=False),
        sa.Column("rack_face", sa.String(10), nullable=False),
        sa.Column("operation_id", sa.String(36), nullable=False),
        sa.Column("return_ids", sa.JSON(), nullable=False),
        sa.Column("response_evidence_id", sa.BigInteger(), nullable=True),
        sa.Column("selected_count", sa.Integer(), nullable=True),
        sa.Column("transport_task_id", sa.String(80), nullable=True),
        sa.Column("transport_result_evidence_id", sa.BigInteger(), nullable=True),
        *[sa.Column(name, sa.DateTime(), nullable=True) for name in ("handed_off_at", "settled_at", "archived_at")],
        sa.PrimaryKeyConstraint("id", name=op.f("pk_automatic_picking_return_allocations")),
        sa.UniqueConstraint("operation_id", name=op.f("uq_automatic_picking_return_allocations_operation_id")),
        sa.CheckConstraint(
            "(response_evidence_id IS NULL AND selected_count IS NULL) OR "
            "(response_evidence_id IS NOT NULL AND selected_count IS NOT NULL AND selected_count BETWEEN 0 AND 4)",
            name=op.f("ck_automatic_picking_return_allocations_automatic_return_allocation_response"),
        ),
    ]
    for column, table, remote in (
        ("workline_id", "work_lines", "id"),
        ("arrival_evidence_id", "inbound_evidences", "id"),
        ("response_evidence_id", "inbound_evidences", "id"),
        ("transport_result_evidence_id", "inbound_evidences", "id"),
        ("transport_task_id", "transport_tasks", "transport_task_id"),
    ):
        columns.append(
            sa.ForeignKeyConstraint(
                [column],
                [f"wes_biz.{table}.{remote}"],
                name=op.f(f"fk_automatic_picking_return_allocations_{column}_{table}"),
            )
        )
    op.create_table("automatic_picking_return_allocations", *columns, schema="wes_biz")
    op.add_column(
        "automatic_picking_returns", sa.Column("allocation_id", sa.BigInteger(), nullable=True), schema="wes_biz"
    )
    op.create_foreign_key(
        op.f("fk_automatic_picking_returns_allocation_id_automatic_picking_return_allocations"),
        "automatic_picking_returns",
        "automatic_picking_return_allocations",
        ["allocation_id"],
        ["id"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )


def downgrade():
    if (
        op.get_bind()
        .execute(sa.text("SELECT EXISTS (SELECT 1 FROM wes_biz.automatic_picking_return_allocations)"))
        .scalar()
    ):
        raise RuntimeError("cannot discard automatic return allocation identities or obligations")
    op.drop_constraint(
        op.f("fk_automatic_picking_returns_allocation_id_automatic_picking_return_allocations"),
        "automatic_picking_returns",
        schema="wes_biz",
        type_="foreignkey",
    )
    op.drop_column("automatic_picking_returns", "allocation_id", schema="wes_biz")
    op.drop_table("automatic_picking_return_allocations", schema="wes_biz")
