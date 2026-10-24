"""persist automatic source departure decision

Revision ID: de97e0860fd5
Revises: 43401479b2cc
Create Date: 2026-10-19 22:46:59.714018+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "de97e0860fd5"
down_revision: Union[str, Sequence[str], None] = "43401479b2cc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "automatic_picking_return_motions",
        sa.Column("departure_retry_at", sa.DateTime(), nullable=True),
        schema="wes_biz",
    )
    op.drop_constraint(
        "automatic_motion_departure_binding", "automatic_picking_return_motions", schema="wes_biz", type_="check"
    )
    op.create_check_constraint(
        "automatic_motion_departure_binding",
        "automatic_picking_return_motions",
        "(departure_operation_id IS NULL AND departure_transport_task_id IS NULL AND departure_result_evidence_id IS NULL AND departure_retry_at IS NULL) OR (departure_operation_id IS NOT NULL AND ((departure_transport_task_id IS NULL AND departure_result_evidence_id IS NULL) OR (departure_transport_task_id IS NOT NULL AND departure_retry_at IS NULL)))",
        schema="wes_biz",
    )


def downgrade() -> None:
    current = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM wes_biz.automatic_picking_return_motions WHERE departure_operation_id IS NOT NULL AND departure_transport_task_id IS NULL LIMIT 1"
            )
        )
        .first()
    )
    if current is not None:
        raise RuntimeError("cannot discard automatic source departure decision")
    op.drop_constraint(
        "automatic_motion_departure_binding", "automatic_picking_return_motions", schema="wes_biz", type_="check"
    )
    op.create_check_constraint(
        "automatic_motion_departure_binding",
        "automatic_picking_return_motions",
        "(departure_operation_id IS NULL AND departure_transport_task_id IS NULL AND departure_result_evidence_id IS NULL) OR (departure_operation_id IS NOT NULL AND departure_transport_task_id IS NOT NULL)",
        schema="wes_biz",
    )
    op.drop_column("automatic_picking_return_motions", "departure_retry_at", schema="wes_biz")
