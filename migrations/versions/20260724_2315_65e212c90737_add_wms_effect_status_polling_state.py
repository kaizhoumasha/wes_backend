"""add wms effect status polling state

Revision ID: 65e212c90737
Revises: 6ea20f0c0d22
Create Date: 2026-07-24 23:15:22.004338+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "65e212c90737"
down_revision: Union[str, Sequence[str], None] = "6ea20f0c0d22"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """增加 WMS EFFECT 语义确认的轮询、lease 与冻结 binding 状态。"""

    op.add_column(
        "runtime_intent_logs",
        sa.Column("status_check_started_at", sa.DateTime(), nullable=True),
        schema="wes_runtime",
    )
    op.add_column(
        "runtime_intent_logs",
        sa.Column("status_check_after", sa.DateTime(), nullable=True),
        schema="wes_runtime",
    )
    op.add_column(
        "runtime_intent_logs",
        sa.Column("status_check_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        schema="wes_runtime",
    )
    op.add_column(
        "runtime_intent_logs",
        sa.Column("status_resubmit_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        schema="wes_runtime",
    )
    op.add_column(
        "runtime_intent_logs",
        sa.Column("status_source_version", sa.BigInteger(), nullable=True),
        schema="wes_runtime",
    )
    op.add_column(
        "runtime_intent_logs",
        sa.Column("status_check_lease_token", sa.String(length=64), nullable=True),
        schema="wes_runtime",
    )
    op.add_column(
        "runtime_intent_logs",
        sa.Column("status_check_lease_until", sa.DateTime(), nullable=True),
        schema="wes_runtime",
    )
    op.add_column(
        "runtime_intent_logs",
        sa.Column(
            "status_binding_snapshot_json",
            sa.JSON(),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
        schema="wes_runtime",
    )
    op.add_column(
        "runtime_intent_logs",
        sa.Column("status_binding_snapshot_hash", sa.String(length=64), nullable=True),
        schema="wes_runtime",
    )
    op.create_index(
        "ix_runtime_intent_log_effect_status_check_after",
        "runtime_intent_logs",
        ["effect_status", "status_check_after"],
        unique=False,
        schema="wes_runtime",
    )


def downgrade() -> None:
    """移除 WMS EFFECT 状态确认调度状态。"""

    op.drop_index(
        "ix_runtime_intent_log_effect_status_check_after",
        table_name="runtime_intent_logs",
        schema="wes_runtime",
    )
    op.drop_column("runtime_intent_logs", "status_binding_snapshot_hash", schema="wes_runtime")
    op.drop_column("runtime_intent_logs", "status_binding_snapshot_json", schema="wes_runtime")
    op.drop_column("runtime_intent_logs", "status_check_lease_until", schema="wes_runtime")
    op.drop_column("runtime_intent_logs", "status_check_lease_token", schema="wes_runtime")
    op.drop_column("runtime_intent_logs", "status_source_version", schema="wes_runtime")
    op.drop_column("runtime_intent_logs", "status_resubmit_count", schema="wes_runtime")
    op.drop_column("runtime_intent_logs", "status_check_count", schema="wes_runtime")
    op.drop_column("runtime_intent_logs", "status_check_after", schema="wes_runtime")
    op.drop_column("runtime_intent_logs", "status_check_started_at", schema="wes_runtime")
