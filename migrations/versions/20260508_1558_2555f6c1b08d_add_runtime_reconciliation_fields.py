"""add runtime reconciliation fields

Revision ID: 2555f6c1b08d
Revises: a1b2c3d4e5f7
Create Date: 2026-05-08 15:58:23.731970+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2555f6c1b08d"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _drop_enum_check_constraint(table_name: str, constraint_name: str) -> None:
    """Drop SQLAlchemy enum check constraints created with or without naming convention."""

    op.execute(f"ALTER TABLE wes_biz.{table_name} DROP CONSTRAINT IF EXISTS ck_{table_name}_{constraint_name}")
    op.execute(f"ALTER TABLE wes_biz.{table_name} DROP CONSTRAINT IF EXISTS {constraint_name}")


def upgrade() -> None:
    """Add ACK-anchored wait and runtime reconciliation fields."""

    op.add_column(
        "workline_sessions",
        sa.Column("current_wait_timeout_seconds", sa.Integer(), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "workline_sessions",
        sa.Column(
            "reconciliation_state",
            sa.Enum(
                "PENDING",
                "RESOLVED",
                name="runtimereconciliationstate",
                schema="wes_biz",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=True,
        ),
        schema="wes_biz",
    )
    op.add_column(
        "workline_sessions",
        sa.Column(
            "reconciliation_reason",
            sa.Enum(
                "CALLBACK_DEADLINE_EXPIRED",
                "COMMAND_ACK_EXHAUSTED",
                "OUTBOX_DISPATCH_FAILED",
                name="runtimereconciliationreason",
                schema="wes_biz",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=True,
        ),
        schema="wes_biz",
    )
    op.add_column(
        "workline_sessions",
        sa.Column(
            "reconciliation_source_kind",
            sa.Enum(
                "TIMER_TIMEOUT",
                "DISPATCH_ACK_EXHAUSTED",
                name="runtimereconciliationsourcekind",
                schema="wes_biz",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=True,
        ),
        schema="wes_biz",
    )
    op.add_column(
        "workline_sessions",
        sa.Column("reconciliation_source_inbox_id", sa.Integer(), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "workline_sessions",
        sa.Column("reconciliation_source_outbox_id", sa.Integer(), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "workline_sessions",
        sa.Column("reconciliation_command_id", sa.Integer(), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "workline_sessions",
        sa.Column("reconciliation_device_id", sa.Integer(), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "workline_sessions",
        sa.Column("reconciliation_wait_token", sa.String(length=200), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "workline_sessions",
        sa.Column("reconciliation_ack_received_at", sa.DateTime(), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "workline_sessions",
        sa.Column("reconciliation_deadline_at", sa.DateTime(), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "workline_sessions",
        sa.Column("reconciliation_occurred_at", sa.DateTime(), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "workline_sessions",
        sa.Column(
            "reconciliation_late_evidence_received",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        schema="wes_biz",
    )
    op.add_column(
        "workline_sessions",
        sa.Column(
            "reconciliation_resolution",
            sa.Enum(
                "COMPLETED",
                "FAILED",
                "CANCELLED",
                name="runtimereconciliationresolution",
                schema="wes_biz",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=True,
        ),
        schema="wes_biz",
    )
    op.add_column(
        "workline_sessions",
        sa.Column("reconciliation_resolved_at", sa.DateTime(), nullable=True),
        schema="wes_biz",
    )

    op.create_index(
        op.f("ix_wes_biz_workline_sessions_reconciliation_state"),
        "workline_sessions",
        ["reconciliation_state"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_workline_sessions_reconciliation_reason"),
        "workline_sessions",
        ["reconciliation_reason"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_workline_sessions_reconciliation_source_inbox_id"),
        "workline_sessions",
        ["reconciliation_source_inbox_id"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_workline_sessions_reconciliation_source_outbox_id"),
        "workline_sessions",
        ["reconciliation_source_outbox_id"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_workline_sessions_reconciliation_command_id"),
        "workline_sessions",
        ["reconciliation_command_id"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_workline_sessions_reconciliation_device_id"),
        "workline_sessions",
        ["reconciliation_device_id"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_workline_sessions_reconciliation_occurred_at"),
        "workline_sessions",
        ["reconciliation_occurred_at"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_workline_sessions_reconciliation_resolved_at"),
        "workline_sessions",
        ["reconciliation_resolved_at"],
        unique=False,
        schema="wes_biz",
    )

    op.add_column(
        "workline_outbox",
        sa.Column("blocked_by_reconciliation_session_id", sa.Integer(), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "workline_outbox",
        sa.Column("blocked_device_id", sa.Integer(), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "workline_outbox",
        sa.Column("blocked_workline_id", sa.Integer(), nullable=True),
        schema="wes_biz",
    )
    op.add_column(
        "workline_outbox",
        sa.Column("blocked_reason", sa.String(length=100), nullable=True),
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_workline_outbox_blocked_by_reconciliation_session_id"),
        "workline_outbox",
        ["blocked_by_reconciliation_session_id"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_workline_outbox_blocked_device_id"),
        "workline_outbox",
        ["blocked_device_id"],
        unique=False,
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_workline_outbox_blocked_workline_id"),
        "workline_outbox",
        ["blocked_workline_id"],
        unique=False,
        schema="wes_biz",
    )

    _drop_enum_check_constraint("workline_outbox", "outboxstatus")
    op.execute("UPDATE wes_biz.workline_outbox SET status = 'SENT' WHERE status = 'ACKED'")
    op.create_check_constraint(
        "outboxstatus",
        "workline_outbox",
        "status IN ('NEW', 'DISPATCHING', 'SENT', 'BLOCKED_RESOURCE', 'FAILED', 'CANCELLED')",
        schema="wes_biz",
    )

    _drop_enum_check_constraint("work_lines", "worklineruntimestatus")
    op.create_check_constraint(
        "worklineruntimestatus",
        "work_lines",
        "runtime_status IN ('READY', 'ESTOPPED', 'RECONCILING')",
        schema="wes_biz",
    )


def downgrade() -> None:
    """Remove runtime reconciliation fields."""

    _drop_enum_check_constraint("work_lines", "worklineruntimestatus")
    op.execute("UPDATE wes_biz.work_lines SET runtime_status = 'ESTOPPED' WHERE runtime_status = 'RECONCILING'")
    op.create_check_constraint(
        "worklineruntimestatus",
        "work_lines",
        "runtime_status IN ('READY', 'ESTOPPED')",
        schema="wes_biz",
    )

    _drop_enum_check_constraint("workline_outbox", "outboxstatus")
    op.execute("UPDATE wes_biz.workline_outbox SET status = 'NEW' WHERE status = 'BLOCKED_RESOURCE'")
    op.create_check_constraint(
        "outboxstatus",
        "workline_outbox",
        "status IN ('NEW', 'DISPATCHING', 'SENT', 'ACKED', 'FAILED', 'CANCELLED')",
        schema="wes_biz",
    )

    op.drop_index(
        op.f("ix_wes_biz_workline_outbox_blocked_workline_id"),
        table_name="workline_outbox",
        schema="wes_biz",
    )
    op.drop_index(
        op.f("ix_wes_biz_workline_outbox_blocked_device_id"),
        table_name="workline_outbox",
        schema="wes_biz",
    )
    op.drop_index(
        op.f("ix_wes_biz_workline_outbox_blocked_by_reconciliation_session_id"),
        table_name="workline_outbox",
        schema="wes_biz",
    )
    op.drop_column("workline_outbox", "blocked_reason", schema="wes_biz")
    op.drop_column("workline_outbox", "blocked_workline_id", schema="wes_biz")
    op.drop_column("workline_outbox", "blocked_device_id", schema="wes_biz")
    op.drop_column("workline_outbox", "blocked_by_reconciliation_session_id", schema="wes_biz")

    op.drop_index(
        op.f("ix_wes_biz_workline_sessions_reconciliation_resolved_at"),
        table_name="workline_sessions",
        schema="wes_biz",
    )
    op.drop_index(
        op.f("ix_wes_biz_workline_sessions_reconciliation_occurred_at"),
        table_name="workline_sessions",
        schema="wes_biz",
    )
    op.drop_index(
        op.f("ix_wes_biz_workline_sessions_reconciliation_device_id"),
        table_name="workline_sessions",
        schema="wes_biz",
    )
    op.drop_index(
        op.f("ix_wes_biz_workline_sessions_reconciliation_command_id"),
        table_name="workline_sessions",
        schema="wes_biz",
    )
    op.drop_index(
        op.f("ix_wes_biz_workline_sessions_reconciliation_source_outbox_id"),
        table_name="workline_sessions",
        schema="wes_biz",
    )
    op.drop_index(
        op.f("ix_wes_biz_workline_sessions_reconciliation_source_inbox_id"),
        table_name="workline_sessions",
        schema="wes_biz",
    )
    op.drop_index(
        op.f("ix_wes_biz_workline_sessions_reconciliation_reason"),
        table_name="workline_sessions",
        schema="wes_biz",
    )
    op.drop_index(
        op.f("ix_wes_biz_workline_sessions_reconciliation_state"),
        table_name="workline_sessions",
        schema="wes_biz",
    )
    op.drop_column("workline_sessions", "reconciliation_resolved_at", schema="wes_biz")
    op.drop_column("workline_sessions", "reconciliation_resolution", schema="wes_biz")
    op.drop_column("workline_sessions", "reconciliation_late_evidence_received", schema="wes_biz")
    op.drop_column("workline_sessions", "reconciliation_occurred_at", schema="wes_biz")
    op.drop_column("workline_sessions", "reconciliation_deadline_at", schema="wes_biz")
    op.drop_column("workline_sessions", "reconciliation_ack_received_at", schema="wes_biz")
    op.drop_column("workline_sessions", "reconciliation_wait_token", schema="wes_biz")
    op.drop_column("workline_sessions", "reconciliation_device_id", schema="wes_biz")
    op.drop_column("workline_sessions", "reconciliation_command_id", schema="wes_biz")
    op.drop_column("workline_sessions", "reconciliation_source_outbox_id", schema="wes_biz")
    op.drop_column("workline_sessions", "reconciliation_source_inbox_id", schema="wes_biz")
    op.drop_column("workline_sessions", "reconciliation_source_kind", schema="wes_biz")
    op.drop_column("workline_sessions", "reconciliation_reason", schema="wes_biz")
    op.drop_column("workline_sessions", "reconciliation_state", schema="wes_biz")
    op.drop_column("workline_sessions", "current_wait_timeout_seconds", schema="wes_biz")
