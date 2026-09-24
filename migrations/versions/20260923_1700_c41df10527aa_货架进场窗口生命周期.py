"""货架进场窗口生命周期

Revision ID: c41df10527aa
Revises: aa4d58c0be72
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "c41df10527aa"
down_revision: Union[str, Sequence[str], None] = "aa4d58c0be72"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for column in (
        sa.Column("window_target_location_code", sa.String(length=120), nullable=True),
        sa.Column("window_departure_client_request_id", sa.String(length=120), nullable=True),
        sa.Column("window_released_at", sa.DateTime(), nullable=True),
    ):
        op.add_column("transport_decision_bindings", column, schema="wes_biz")
    op.create_index(
        "ux_transport_decision_bindings_active_inbound_rack",
        "transport_decision_bindings",
        ["workline_id", "resource_fence_id"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("window_target_location_code IS NOT NULL AND window_released_at IS NULL"),
    )
    op.create_index(
        "ix_transport_decision_bindings_window_departure",
        "transport_decision_bindings",
        ["window_departure_client_request_id"],
        schema="wes_biz",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_transport_decision_bindings_window_departure", table_name="transport_decision_bindings", schema="wes_biz"
    )
    op.drop_index(
        "ux_transport_decision_bindings_active_inbound_rack", table_name="transport_decision_bindings", schema="wes_biz"
    )
    for column in ("window_released_at", "window_departure_client_request_id", "window_target_location_code"):
        op.drop_column("transport_decision_bindings", column, schema="wes_biz")
