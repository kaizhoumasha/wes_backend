"""handoff automatic drain departures

Revision ID: 77b256c269b3
Revises: 588d54e9fe3e
Create Date: 2026-10-20 05:48:48.298407+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "77b256c269b3"
down_revision: Union[str, Sequence[str], None] = "588d54e9fe3e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "automatic_picking_drains",
        sa.Column("departure_handoffs", sa.JSON(), nullable=False, server_default="{}"),
        schema="wes_biz",
    )
    op.alter_column("automatic_picking_drains", "departure_handoffs", server_default=None, schema="wes_biz")


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM wes_biz.automatic_picking_drains WHERE departure_handoffs::jsonb <> '{}'::jsonb)"
            )
        )
        .scalar()
    ):
        raise RuntimeError("cannot discard automatic drain departure handoffs")
    op.drop_column("automatic_picking_drains", "departure_handoffs", schema="wes_biz")
