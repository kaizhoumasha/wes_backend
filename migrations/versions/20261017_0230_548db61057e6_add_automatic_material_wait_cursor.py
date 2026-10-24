"""add_automatic_material_wait_cursor

Revision ID: 548db61057e6
Revises: 807514a7776b
Create Date: 2026-10-17 02:30:02.176314+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "548db61057e6"
down_revision: Union[str, Sequence[str], None] = "807514a7776b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "automatic_picking_pickups",
        sa.Column("material_confirmation_id", sa.BigInteger(), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "automatic_picking_pickups", sa.Column("material_retry_at", sa.DateTime(), nullable=True), schema="wes_biz"
    )
    op.create_foreign_key(
        op.f("fk_automatic_picking_pickups_material_confirmation_id_wms_confirmations"),
        "automatic_picking_pickups",
        "wms_confirmations",
        ["material_confirmation_id"],
        ["id"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )


def downgrade() -> None:
    """Downgrade schema."""
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM wes_biz.automatic_picking_pickups WHERE material_confirmation_id IS NOT NULL OR material_retry_at IS NOT NULL)"
            )
        )
        .scalar()
    ):
        raise RuntimeError("cannot discard active material decide cursor")
    op.drop_constraint(
        op.f("fk_automatic_picking_pickups_material_confirmation_id_wms_confirmations"),
        "automatic_picking_pickups",
        schema="wes_biz",
        type_="foreignkey",
    )
    op.drop_column("automatic_picking_pickups", "material_retry_at", schema="wes_biz")
    op.drop_column("automatic_picking_pickups", "material_confirmation_id", schema="wes_biz")
