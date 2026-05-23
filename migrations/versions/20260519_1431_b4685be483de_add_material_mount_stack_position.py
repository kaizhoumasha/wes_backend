"""add material mount stack position

Revision ID: b4685be483de
Revises: 286ddc5bc27d
Create Date: 2026-05-19 14:31:23.660706+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4685be483de"
down_revision: Union[str, Sequence[str], None] = "286ddc5bc27d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "wes_biz"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "resource_bin_material_mounts",
        sa.Column(
            "cell_stack_position",
            sa.Integer(),
            server_default="1",
            nullable=False,
            comment="同一料格内入格顺序，1 为最早入格",
        ),
        schema=SCHEMA,
    )
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    id,
                    row_number() OVER (
                        PARTITION BY bin_cell_occupancy_id
                        ORDER BY started_at ASC, id ASC
                    ) AS stack_position
                FROM "wes_biz"."resource_bin_material_mounts"
                WHERE bin_cell_occupancy_id IS NOT NULL
            )
            UPDATE "wes_biz"."resource_bin_material_mounts" AS mounts
            SET cell_stack_position = ranked.stack_position
            FROM ranked
            WHERE mounts.id = ranked.id
            """
        )
    )
    op.alter_column(
        "resource_bin_material_mounts",
        "cell_stack_position",
        server_default=None,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wes_biz_resource_bin_material_mounts_cell_stack_position",
        "resource_bin_material_mounts",
        ["cell_stack_position"],
        schema=SCHEMA,
    )
    op.create_index(
        "ux_resource_bin_material_mounts_active_stack_position",
        "resource_bin_material_mounts",
        ["bin_cell_occupancy_id", "cell_stack_position"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("ended_at IS NULL AND bin_cell_occupancy_id IS NOT NULL"),
    )
    op.create_index(
        "ix_resource_bin_material_mounts_cell_stack_active",
        "resource_bin_material_mounts",
        ["bin_code", "bin_cell_index", "cell_stack_position", "ended_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_resource_bin_material_mounts_cell_stack_active",
        table_name="resource_bin_material_mounts",
        schema=SCHEMA,
    )
    op.drop_index(
        "ux_resource_bin_material_mounts_active_stack_position",
        table_name="resource_bin_material_mounts",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_wes_biz_resource_bin_material_mounts_cell_stack_position",
        table_name="resource_bin_material_mounts",
        schema=SCHEMA,
    )
    op.drop_column("resource_bin_material_mounts", "cell_stack_position", schema=SCHEMA)
