"""按计划版本区分重复来源成员

Revision ID: dfd0c2e671d1
Revises: 4ef64c642313
Create Date: 2026-09-24 00:58:37.571169+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "dfd0c2e671d1"
down_revision: Union[str, Sequence[str], None] = "4ef64c642313"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    for table, index, columns in (
        (
            "direct_pick_executions",
            "ux_direct_pick_source",
            ["picking_task_id", "plan_revision", "rack_id", "rack_face", "slot_id"],
        ),
        (
            "picking_task_bin_source_racks",
            "ux_picking_bin_source",
            ["picking_task_id", "plan_revision", "rack_id", "rack_face"],
        ),
    ):
        op.drop_index(index, table_name=table, schema="wes_biz")
        op.create_index(index, table, columns, unique=True, schema="wes_biz")
    op.add_column(
        "direct_pick_face_completions", sa.Column("plan_revision", sa.BigInteger(), nullable=True), schema="wes_biz"
    )
    op.execute("""
        UPDATE wes_biz.direct_pick_face_completions AS completion
        SET plan_revision = (
            SELECT MIN(pick.plan_revision)
            FROM wes_biz.direct_pick_executions AS pick
            WHERE pick.picking_task_id = completion.picking_task_id
              AND pick.rack_id = completion.rack_id
              AND pick.rack_face = completion.rack_face
            HAVING COUNT(DISTINCT pick.plan_revision) = 1
        )
    """)
    op.alter_column("direct_pick_face_completions", "plan_revision", nullable=False, schema="wes_biz")
    op.drop_index("ux_direct_pick_face_completion", table_name="direct_pick_face_completions", schema="wes_biz")
    op.create_index(
        "ux_direct_pick_face_completion",
        "direct_pick_face_completions",
        ["picking_task_id", "plan_revision", "rack_id", "rack_face"],
        unique=True,
        schema="wes_biz",
    )


def downgrade() -> None:
    """Downgrade schema."""
    for table, index, columns in (
        ("direct_pick_executions", "ux_direct_pick_source", ["picking_task_id", "rack_id", "rack_face", "slot_id"]),
        ("picking_task_bin_source_racks", "ux_picking_bin_source", ["picking_task_id", "rack_id", "rack_face"]),
    ):
        op.drop_index(index, table_name=table, schema="wes_biz")
        op.create_index(index, table, columns, unique=True, schema="wes_biz")
    op.drop_index("ux_direct_pick_face_completion", table_name="direct_pick_face_completions", schema="wes_biz")
    op.drop_column("direct_pick_face_completions", "plan_revision", schema="wes_biz")
    op.create_index(
        "ux_direct_pick_face_completion",
        "direct_pick_face_completions",
        ["picking_task_id", "rack_id", "rack_face"],
        unique=True,
        schema="wes_biz",
    )
