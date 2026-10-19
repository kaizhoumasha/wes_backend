"""handoff automatic drain return members

Revision ID: 588d54e9fe3e
Revises: 40e50581119c
Create Date: 2026-10-20 02:39:18.382913+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "588d54e9fe3e"
down_revision: Union[str, Sequence[str], None] = "40e50581119c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "automatic_picking_drains",
        sa.Column("returned_ids", sa.JSON(), nullable=False, server_default="[]"),
        schema="wes_biz",
    )
    op.alter_column("automatic_picking_drains", "returned_ids", server_default=None, schema="wes_biz")
    op.add_column(
        "automatic_picking_return_allocations", sa.Column("drain_id", sa.BigInteger(), nullable=True), schema="wes_biz"
    )
    op.create_foreign_key(
        op.f("fk_automatic_picking_return_allocations_drain_id_automatic_picking_drains"),
        "automatic_picking_return_allocations",
        "automatic_picking_drains",
        ["drain_id"],
        ["id"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )
    op.create_index(
        "ix_wes_biz_automatic_picking_return_allocations_drain_id",
        "automatic_picking_return_allocations",
        ["drain_id"],
        schema="wes_biz",
    )


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(
            sa.text("""
        SELECT EXISTS (SELECT 1 FROM wes_biz.automatic_picking_return_allocations WHERE drain_id IS NOT NULL)
            OR EXISTS (SELECT 1 FROM wes_biz.automatic_picking_drains WHERE returned_ids::jsonb <> '[]'::jsonb)
    """)
        )
        .scalar()
    ):
        raise RuntimeError("cannot discard automatic drain return ownership or successful member facts")
    op.drop_index(
        "ix_wes_biz_automatic_picking_return_allocations_drain_id",
        table_name="automatic_picking_return_allocations",
        schema="wes_biz",
    )
    op.drop_constraint(
        op.f("fk_automatic_picking_return_allocations_drain_id_automatic_picking_drains"),
        "automatic_picking_return_allocations",
        type_="foreignkey",
        schema="wes_biz",
    )
    op.drop_column("automatic_picking_return_allocations", "drain_id", schema="wes_biz")
    op.drop_column("automatic_picking_drains", "returned_ids", schema="wes_biz")
