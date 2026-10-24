"""continue automatic drain current

Revision ID: c094b575576f
Revises: 77b256c269b3
Create Date: 2026-10-20 23:04:11.656542+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c094b575576f"
down_revision: Union[str, Sequence[str], None] = "77b256c269b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "automatic_picking_drains", sa.Column("request_return_ids", sa.JSON(), nullable=True), schema="wes_biz"
    )
    op.execute("UPDATE wes_biz.automatic_picking_drains SET request_return_ids = return_ids")
    op.alter_column("automatic_picking_drains", "request_return_ids", nullable=False, schema="wes_biz")
    op.add_column(
        "automatic_picking_drains",
        sa.Column("started_operation_id", sa.String(length=36), nullable=True),
        schema="wes_biz",
    )


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM wes_biz.automatic_picking_drains "
                "WHERE request_return_ids::jsonb <> return_ids::jsonb "
                "OR started_operation_id <> operation_id)"
            )
        )
        .scalar()
    ):
        raise RuntimeError("cannot discard automatic drain continuation responsibility")
    op.drop_column("automatic_picking_drains", "started_operation_id", schema="wes_biz")
    op.drop_column("automatic_picking_drains", "request_return_ids", schema="wes_biz")
