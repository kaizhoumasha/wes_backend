"""retired rack task metadata draft before rack domain consolidation

Revision ID: 97dbf218ed9f
Revises: 083e85d1bf93
Create Date: 2026-05-21 15:13:15.977851+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "97dbf218ed9f"
down_revision: Union[str, Sequence[str], None] = "083e85d1bf93"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "wes_biz"


def _drop_constraint_if_exists(table_name: str, constraint_name: str) -> None:
    op.execute(sa.text(f'ALTER TABLE "{SCHEMA}"."{table_name}" DROP CONSTRAINT IF EXISTS "{constraint_name}"'))


def _drop_index_if_exists(index_name: str) -> None:
    op.execute(sa.text(f'DROP INDEX IF EXISTS "{SCHEMA}"."{index_name}"'))


def _drop_workline_rack_position_role_constraints() -> None:
    for constraint_name in (
        "worklinerackpositionrole",
        "ck_workline_rack_positions_worklinerackpositionrole",
        "ck_workline_rack_positions_position_role",
        "ck_workline_rack_positions_ck_workline_rack_positions_c_65eb",
    ):
        _drop_constraint_if_exists("workline_rack_positions", constraint_name)


def upgrade() -> None:
    """Retain workline_rack_positions schema changes that are not handled by rack domain."""
    _drop_index_if_exists("ux_resource_rack_placements_active_workline_position")
    op.create_index(
        "ix_resource_rack_placements_workline_position_active",
        "resource_rack_placements",
        ["workline_code", "position_code", "ended_at"],
        schema=SCHEMA,
    )

    _drop_workline_rack_position_role_constraints()

    op.execute(
        sa.text(
            """
            UPDATE "wes_biz"."workline_rack_positions"
            SET position_role = CASE position_role
                WHEN 'SOURCE_STORAGE' THEN 'SMT_CLASSIFIER_SINGLE_RACK_WORK'
                WHEN 'OUTPUT_BUFFER' THEN 'SMT_RACK_EXCHANGE_AREA'
                ELSE position_role
            END
            WHERE position_role IN ('SOURCE_STORAGE', 'OUTPUT_BUFFER')
            """
        )
    )

    op.create_check_constraint(
        "worklinerackpositionrole",
        "workline_rack_positions",
        "position_role IN ('SMT_CLASSIFIER_SINGLE_RACK_WORK', 'SMT_RACK_EXCHANGE_AREA', 'SMT_SORTER_QUEUE', 'SMT_SORTER_STATION', 'SMT_EMPTY_RACK_AREA')",
        schema=SCHEMA,
    )
    _drop_constraint_if_exists("workline_rack_positions", "ck_workline_rack_positions_capacity_one")
    op.create_check_constraint(
        "ck_workline_rack_positions_capacity_positive",
        "workline_rack_positions",
        "capacity > 0",
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Downgrade schema."""
    _drop_index_if_exists("ix_resource_rack_placements_workline_position_active")
    op.create_index(
        "ux_resource_rack_placements_active_workline_position",
        "resource_rack_placements",
        ["workline_code", "position_code"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("ended_at IS NULL AND workline_code IS NOT NULL AND position_code IS NOT NULL"),
    )

    _drop_workline_rack_position_role_constraints()
    _drop_constraint_if_exists("workline_rack_positions", "ck_workline_rack_positions_capacity_positive")
    op.execute(
        sa.text(
            """
            UPDATE "wes_biz"."workline_rack_positions"
            SET position_role = CASE position_role
                WHEN 'SMT_RACK_EXCHANGE_AREA' THEN 'OUTPUT_BUFFER'
                WHEN 'SMT_EMPTY_RACK_AREA' THEN 'OUTPUT_BUFFER'
                ELSE 'SOURCE_STORAGE'
            END
            WHERE position_role NOT IN ('SOURCE_STORAGE', 'OUTPUT_BUFFER')
            """
        )
    )
    op.create_check_constraint(
        "worklinerackpositionrole",
        "workline_rack_positions",
        "position_role IN ('SOURCE_STORAGE', 'OUTPUT_BUFFER')",
        schema=SCHEMA,
    )
    op.execute(sa.text('UPDATE "wes_biz"."workline_rack_positions" SET capacity = 1 WHERE capacity <> 1'))
    op.create_check_constraint(
        "ck_workline_rack_positions_capacity_one",
        "workline_rack_positions",
        "capacity = 1",
        schema=SCHEMA,
    )
