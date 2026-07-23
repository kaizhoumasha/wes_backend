"""add system outbox dispatch concurrency

Revision ID: 2c1407a3606e
Revises: c325aab03400
Create Date: 2026-07-23 01:08:44.017360+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2c1407a3606e"
down_revision: Union[str, Sequence[str], None] = "c325aab03400"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """新增显式调度 identity 与有限 owner lease。"""

    op.add_column(
        "system_outbox",
        sa.Column("provider_profile_identity", sa.String(length=240), nullable=False),
        schema="wes_biz",
    )
    op.add_column(
        "system_outbox",
        sa.Column("operation_identity", sa.String(length=240), nullable=False),
        schema="wes_biz",
    )
    op.add_column(
        "system_outbox",
        sa.Column("lease_owner_token", sa.String(length=240), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "system_outbox",
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "system_outbox",
        sa.Column("dispatch_started_at", sa.DateTime(), nullable=True),
        schema="wes_biz",
    )
    op.create_check_constraint(
        op.f("ck_system_outbox_ck_system_outbox_dispatch_lease_shape"),
        "system_outbox",
        "(status != 'DISPATCHING' OR (lease_owner_token IS NOT NULL AND lease_expires_at IS NOT NULL)) "
        "AND (status = 'DISPATCHING' OR lease_expires_at IS NULL)",
        schema="wes_biz",
    )
    op.create_index(
        "ix_system_outbox_dispatch_bucket_claim",
        "system_outbox",
        ["provider_profile_identity", "operation_identity", "status", "next_retry_at", "created_at"],
        schema="wes_biz",
    )
    op.create_index(
        "ix_system_outbox_active_lease",
        "system_outbox",
        ["provider_profile_identity", "operation_identity", "status", "lease_expires_at"],
        schema="wes_biz",
    )

    op.add_column(
        "workline_dispatch_attempts",
        sa.Column("lease_expires_at", sa.DateTime(), nullable=False),
        schema="wes_biz",
    )
    op.create_check_constraint(
        op.f("ck_workline_dispatch_attempts_ck_workline_dispatch_attempt_lease_expiry"),
        "workline_dispatch_attempts",
        "length(lease_token) > 0 AND lease_expires_at IS NOT NULL",
        schema="wes_biz",
    )
    op.create_index(
        "ix_workline_dispatch_attempt_outbox_lease",
        "workline_dispatch_attempts",
        ["outbox_id", "lease_token", "status"],
        schema="wes_biz",
    )


def downgrade() -> None:
    """移除显式调度 identity 与有限 owner lease。"""

    op.drop_index(
        "ix_workline_dispatch_attempt_outbox_lease",
        table_name="workline_dispatch_attempts",
        schema="wes_biz",
    )
    op.drop_constraint(
        op.f("ck_workline_dispatch_attempts_ck_workline_dispatch_attempt_lease_expiry"),
        "workline_dispatch_attempts",
        schema="wes_biz",
        type_="check",
    )
    op.drop_column("workline_dispatch_attempts", "lease_expires_at", schema="wes_biz")

    op.drop_index(
        "ix_system_outbox_active_lease",
        table_name="system_outbox",
        schema="wes_biz",
    )
    op.drop_index(
        "ix_system_outbox_dispatch_bucket_claim",
        table_name="system_outbox",
        schema="wes_biz",
    )
    op.drop_constraint(
        op.f("ck_system_outbox_ck_system_outbox_dispatch_lease_shape"),
        "system_outbox",
        schema="wes_biz",
        type_="check",
    )
    op.drop_column("system_outbox", "dispatch_started_at", schema="wes_biz")
    op.drop_column("system_outbox", "lease_expires_at", schema="wes_biz")
    op.drop_column("system_outbox", "lease_owner_token", schema="wes_biz")
    op.drop_column("system_outbox", "operation_identity", schema="wes_biz")
    op.drop_column("system_outbox", "provider_profile_identity", schema="wes_biz")
