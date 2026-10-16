"""add_automatic_pickup_put_command_reference

Revision ID: 4e30101440d2
Revises: 548db61057e6
Create Date: 2026-10-17 03:28:50.379972+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4e30101440d2"
down_revision: Union[str, Sequence[str], None] = "548db61057e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "automatic_picking_pickups", sa.Column("put_command_code", sa.String(80), nullable=True), schema="wes_biz"
    )
    op.create_foreign_key(
        op.f("fk_automatic_picking_pickups_put_command_code_device_commands"),
        "automatic_picking_pickups",
        "device_commands",
        ["put_command_code"],
        ["command_code"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )
    op.create_unique_constraint(
        op.f("uq_automatic_picking_pickups_put_command_code"),
        "automatic_picking_pickups",
        ["put_command_code"],
        schema="wes_biz",
    )


def downgrade() -> None:
    """Downgrade schema."""
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM wes_biz.automatic_picking_pickups WHERE put_command_code IS NOT NULL)")
    ):
        raise RuntimeError("cannot discard active PUT Command reference")
    op.drop_constraint(
        op.f("uq_automatic_picking_pickups_put_command_code"),
        "automatic_picking_pickups",
        schema="wes_biz",
        type_="unique",
    )
    op.drop_constraint(
        op.f("fk_automatic_picking_pickups_put_command_code_device_commands"),
        "automatic_picking_pickups",
        schema="wes_biz",
        type_="foreignkey",
    )
    op.drop_column("automatic_picking_pickups", "put_command_code", schema="wes_biz")
