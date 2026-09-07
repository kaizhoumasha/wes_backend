"""constrain_face_length_to_ten

Revision ID: 3d040b37c049
Revises: 864351b8d0c6
Create Date: 2026-09-06 16:30:41.492367+08:00

"""
# ruff: noqa: S608 -- SQL 标识符仅来自本迁移的固定 FACE_COLUMNS。

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3d040b37c049"
down_revision: Union[str, Sequence[str], None] = "864351b8d0c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


FACE_COLUMNS = (
    ("wes_runtime", "transport_members", "arrival_face"),
    ("wes_runtime", "transport_debug_position_projections", "arrival_face"),
    ("wes_biz", "position_projections", "arrival_face"),
    ("wes_biz", "picking_tasks", "target_rack_face"),
    ("wes_biz", "direct_pick_executions", "rack_face"),
    ("wes_biz", "picking_task_bin_source_racks", "rack_face"),
)


def upgrade() -> None:
    # 同事务先锁全部目标，避免预检后并发写入超长值；不裁剪或重写业务事实。
    op.execute(
        "LOCK TABLE "
        + ", ".join(f"{schema}.{table}" for schema, table, _ in FACE_COLUMNS)
        + " IN ACCESS EXCLUSIVE MODE"
    )
    for schema, table, column in FACE_COLUMNS:
        op.execute(f"""DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM {schema}.{table}
                WHERE {column} IS NOT NULL AND (char_length({column}) < 1 OR char_length({column}) > 10)) THEN
                RAISE EXCEPTION 'face length preflight failed: {schema}.{table}.{column} requires 1..10 characters';
            END IF;
        END $$""")
    op.drop_index("ux_direct_pick_source", table_name="direct_pick_executions", schema="wes_biz")
    op.drop_index("ux_picking_bin_source", table_name="picking_task_bin_source_racks", schema="wes_biz")
    for schema, table, column in FACE_COLUMNS:
        op.alter_column(table, column, existing_type=sa.Text(), type_=sa.String(10), schema=schema)
    for schema, table, column in FACE_COLUMNS[:4]:
        op.create_check_constraint(
            f"{column}_nonempty", table, f"{column} IS NULL OR length({column}) >= 1", schema=schema
        )
    op.create_index(
        "ux_direct_pick_source",
        "direct_pick_executions",
        ["picking_task_id", "rack_id", "rack_face", "slot_id"],
        unique=True,
        schema="wes_biz",
    )
    op.create_index(
        "ux_picking_bin_source",
        "picking_task_bin_source_racks",
        ["picking_task_id", "rack_id", "rack_face"],
        unique=True,
        schema="wes_biz",
    )


def downgrade() -> None:
    op.drop_index("ux_direct_pick_source", table_name="direct_pick_executions", schema="wes_biz")
    op.drop_index("ux_picking_bin_source", table_name="picking_task_bin_source_racks", schema="wes_biz")
    for schema, table, column in FACE_COLUMNS[:4]:
        op.drop_constraint(op.f(f"ck_{table}_{column}_nonempty"), table, schema=schema, type_="check")
    for schema, table, column in FACE_COLUMNS:
        op.alter_column(table, column, existing_type=sa.String(10), type_=sa.Text(), schema=schema)
    # 恢复前一 revision 的索引；共享 pgcrypto 扩展由历史迁移拥有，始终保留。
    op.create_index(
        "ux_direct_pick_source",
        "direct_pick_executions",
        ["picking_task_id", "rack_id", sa.text("public.digest(rack_face, 'sha256')"), "slot_id"],
        unique=True,
        schema="wes_biz",
    )
    op.create_index(
        "ux_picking_bin_source",
        "picking_task_bin_source_racks",
        ["picking_task_id", "rack_id", sa.text("public.digest(rack_face, 'sha256')")],
        unique=True,
        schema="wes_biz",
    )
