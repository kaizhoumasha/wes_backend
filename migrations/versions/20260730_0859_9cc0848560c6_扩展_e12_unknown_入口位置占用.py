"""扩展 E12 UNKNOWN 入口位置占用

Revision ID: 9cc0848560c6
Revises: 8612c6926f4c
Create Date: 2026-07-30 08:59:29.484032+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9cc0848560c6"
down_revision: Union[str, Sequence[str], None] = "8612c6926f4c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """显式记录 entry reservation 释放，并只保留未释放 UNKNOWN 的位置占用。"""

    op.drop_index(
        "ux_wms_conveyor_batch_members_active_inbound_position",
        table_name="wms_conveyor_batch_members",
        schema="wes_runtime",
    )
    op.drop_constraint(
        op.f("ck_wms_conveyor_batch_members_lifecycle"),
        "wms_conveyor_batch_members",
        schema="wes_runtime",
        type_="check",
    )
    op.alter_column(
        "wms_conveyor_batch_members",
        "released_at_ms",
        new_column_name="reservation_released_at_ms",
        schema="wes_runtime",
    )
    op.create_check_constraint(
        op.f("ck_wms_conveyor_batch_members_lifecycle"),
        "wms_conveyor_batch_members",
        "("
        "member_state = 'CANDIDATE' AND accepted_at_ms IS NULL "
        "AND reservation_released_at_ms IS NULL AND terminal_at_ms IS NULL "
        "AND terminal_outcome IS NULL"
        ") OR ("
        "member_state = 'ACCEPTED' AND accepted_at_ms IS NOT NULL "
        "AND reservation_released_at_ms IS NULL AND terminal_at_ms IS NULL "
        "AND terminal_outcome IS NULL"
        ") OR ("
        "member_state = 'RELEASED' AND accepted_at_ms IS NULL "
        "AND reservation_released_at_ms IS NOT NULL AND terminal_at_ms IS NULL "
        "AND terminal_outcome IS NULL"
        ") OR ("
        "member_state = 'TERMINAL' AND accepted_at_ms IS NOT NULL "
        "AND terminal_at_ms IS NOT NULL AND terminal_outcome IS NOT NULL"
        ")",
        schema="wes_runtime",
    )
    op.create_index(
        "ux_wms_conveyor_batch_members_active_inbound_position",
        "wms_conveyor_batch_members",
        ["workline_id", "queue_code", "reserved_queue_position"],
        unique=True,
        schema="wes_runtime",
        postgresql_where=sa.text(
            "direction = 'INBOUND' AND ("
            "member_state IN ('CANDIDATE', 'ACCEPTED') "
            "OR (member_state = 'TERMINAL' AND terminal_outcome = 'UNKNOWN' "
            "AND reservation_released_at_ms IS NULL)"
            ")"
        ),
    )


def downgrade() -> None:
    """恢复只由 pending member 占用入口位置的旧谓词。"""

    op.drop_index(
        "ux_wms_conveyor_batch_members_active_inbound_position",
        table_name="wms_conveyor_batch_members",
        schema="wes_runtime",
    )
    op.drop_constraint(
        op.f("ck_wms_conveyor_batch_members_lifecycle"),
        "wms_conveyor_batch_members",
        schema="wes_runtime",
        type_="check",
    )
    op.alter_column(
        "wms_conveyor_batch_members",
        "reservation_released_at_ms",
        new_column_name="released_at_ms",
        schema="wes_runtime",
    )
    op.execute(
        sa.text(
            "UPDATE wes_runtime.wms_conveyor_batch_members SET released_at_ms = NULL WHERE member_state = 'TERMINAL'"
        )
    )
    op.create_check_constraint(
        op.f("ck_wms_conveyor_batch_members_lifecycle"),
        "wms_conveyor_batch_members",
        "("
        "member_state = 'CANDIDATE' AND accepted_at_ms IS NULL "
        "AND released_at_ms IS NULL AND terminal_at_ms IS NULL "
        "AND terminal_outcome IS NULL"
        ") OR ("
        "member_state = 'ACCEPTED' AND accepted_at_ms IS NOT NULL "
        "AND released_at_ms IS NULL AND terminal_at_ms IS NULL "
        "AND terminal_outcome IS NULL"
        ") OR ("
        "member_state = 'RELEASED' AND accepted_at_ms IS NULL "
        "AND released_at_ms IS NOT NULL AND terminal_at_ms IS NULL "
        "AND terminal_outcome IS NULL"
        ") OR ("
        "member_state = 'TERMINAL' AND accepted_at_ms IS NOT NULL "
        "AND released_at_ms IS NULL AND terminal_at_ms IS NOT NULL "
        "AND terminal_outcome IS NOT NULL"
        ")",
        schema="wes_runtime",
    )
    op.create_index(
        "ux_wms_conveyor_batch_members_active_inbound_position",
        "wms_conveyor_batch_members",
        ["workline_id", "queue_code", "reserved_queue_position"],
        unique=True,
        schema="wes_runtime",
        postgresql_where=sa.text("direction = 'INBOUND' AND member_state IN ('CANDIDATE', 'ACCEPTED')"),
    )
