"""add E12 entry membership constraints

Revision ID: 8612c6926f4c
Revises: f9ffbef8992a
Create Date: 2026-07-30 08:11:58.263116+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8612c6926f4c"
down_revision: Union[str, Sequence[str], None] = "f9ffbef8992a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "wes_runtime"


def upgrade() -> None:
    """为 ENTRY active membership 增加 shape 与位置互斥约束。"""

    op.create_check_constraint(
        op.f("ck_conveyor_queue_memberships_entry_shape"),
        "conveyor_queue_memberships",
        "NOT (membership_status IN ('ACTIVE', 'RECONCILING') "
        "AND queue_role = 'ENTRY') OR ("
        "route_instance_id IS NOT NULL "
        "AND queue_position IS NOT NULL "
        "AND queue_position > 0 "
        "AND bin_code IS NOT NULL"
        ")",
        schema=SCHEMA,
    )
    op.create_index(
        "ux_wes_runtime_conveyor_queue_memberships_active_entry_position",
        "conveyor_queue_memberships",
        ["workline_id", "queue_code", "queue_position"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("membership_status IN ('ACTIVE', 'RECONCILING') AND queue_role = 'ENTRY'"),
    )


def downgrade() -> None:
    """移除 ENTRY active membership 约束。"""

    op.drop_index(
        "ux_wes_runtime_conveyor_queue_memberships_active_entry_position",
        table_name="conveyor_queue_memberships",
        schema=SCHEMA,
    )
    op.drop_constraint(
        op.f("ck_conveyor_queue_memberships_entry_shape"),
        "conveyor_queue_memberships",
        schema=SCHEMA,
        type_="check",
    )
