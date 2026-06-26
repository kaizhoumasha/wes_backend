"""add remaining runtime orchestration tables

Revision ID: f04718a3f04f
Revises: c0bccb9de6f3
Create Date: 2026-06-26 17:19:20.993307+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f04718a3f04f"
down_revision: Union[str, Sequence[str], None] = "c0bccb9de6f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "wes_runtime"


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"')

    op.create_table(
        "execution_work_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("execution_session_id", sa.Integer(), nullable=False),
        sa.Column("correlation_id", sa.String(length=120), nullable=False),
        sa.Column("object_type", sa.String(length=60), nullable=False),
        sa.Column("object_key", sa.String(length=160), nullable=False),
        sa.Column("current_step", sa.String(length=120), nullable=False),
        sa.Column("step_status", sa.String(length=20), nullable=False),
        sa.Column("parent_correlation_id", sa.String(length=120), nullable=True),
        sa.Column("lease_expires_at", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["execution_session_id"], [f"{SCHEMA}.execution_sessions.id"]),
        sa.ForeignKeyConstraint(["correlation_id"], [f"{SCHEMA}.execution_correlations.correlation_id"]),
        sa.ForeignKeyConstraint(["parent_correlation_id"], [f"{SCHEMA}.execution_work_items.correlation_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("correlation_id", name="uq_wes_runtime_execution_work_items_correlation_id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wes_runtime_execution_work_items_execution_session_id",
        "execution_work_items",
        ["execution_session_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wes_runtime_execution_work_items_object_key",
        "execution_work_items",
        ["object_key"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wes_runtime_execution_work_items_parent_correlation_id",
        "execution_work_items",
        ["parent_correlation_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "runtime_inbox",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("execution_session_id", sa.Integer(), nullable=True),
        sa.Column("correlation_id", sa.String(length=120), nullable=True),
        sa.Column("provider_code", sa.String(length=60), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("source_event_id", sa.String(length=160), nullable=True),
        sa.Column("payload_hash", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_retries", sa.Integer(), nullable=False),
        sa.Column("next_retry_at", sa.Integer(), nullable=True),
        sa.Column("lease_until", sa.Integer(), nullable=True),
        sa.Column("last_error_code", sa.String(length=120), nullable=True),
        sa.Column("last_error_message", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(["execution_session_id"], [f"{SCHEMA}.execution_sessions.id"]),
        sa.ForeignKeyConstraint(["correlation_id"], [f"{SCHEMA}.execution_correlations.correlation_id"]),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ux_wes_runtime_runtime_inbox_source_event",
        "runtime_inbox",
        ["provider_code", "event_type", "source_event_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("source_event_id IS NOT NULL"),
    )
    for column_name in (
        "execution_session_id",
        "correlation_id",
        "provider_code",
        "event_type",
        "source_event_id",
        "status",
    ):
        op.create_index(
            f"ix_wes_runtime_runtime_inbox_{column_name}",
            "runtime_inbox",
            [column_name],
            schema=SCHEMA,
        )

    op.create_table(
        "runtime_intent_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("execution_session_id", sa.Integer(), nullable=False),
        sa.Column("correlation_id", sa.String(length=120), nullable=False),
        sa.Column("provider_code", sa.String(length=60), nullable=False),
        sa.Column("target_domain", sa.String(length=60), nullable=False),
        sa.Column("target_action", sa.String(length=120), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("request_hash", sa.String(length=128), nullable=False),
        sa.Column("dispatch_status", sa.String(length=20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(length=120), nullable=True),
        sa.Column("last_error_message", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(["execution_session_id"], [f"{SCHEMA}.execution_sessions.id"]),
        sa.ForeignKeyConstraint(["correlation_id"], [f"{SCHEMA}.execution_correlations.correlation_id"]),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    for column_name in (
        "execution_session_id",
        "correlation_id",
        "provider_code",
        "idempotency_key",
        "dispatch_status",
    ):
        op.create_index(
            f"ix_wes_runtime_runtime_intent_logs_{column_name}",
            "runtime_intent_logs",
            [column_name],
            schema=SCHEMA,
        )

    op.create_table(
        "runtime_timelines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("execution_session_id", sa.Integer(), nullable=False),
        sa.Column("trace_id", sa.String(length=120), nullable=False),
        sa.Column("correlation_id", sa.String(length=120), nullable=True),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("occurred_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["execution_session_id"], [f"{SCHEMA}.execution_sessions.id"]),
        sa.ForeignKeyConstraint(["correlation_id"], [f"{SCHEMA}.execution_correlations.correlation_id"]),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    for column_name in ("execution_session_id", "trace_id", "correlation_id", "event_type", "occurred_at"):
        op.create_index(
            f"ix_wes_runtime_runtime_timelines_{column_name}",
            "runtime_timelines",
            [column_name],
            schema=SCHEMA,
        )

    op.create_table(
        "runtime_holds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("execution_session_id", sa.Integer(), nullable=False),
        sa.Column("correlation_id", sa.String(length=120), nullable=True),
        sa.Column("reason", sa.String(length=200), nullable=False),
        sa.Column("hold_type", sa.String(length=60), nullable=False),
        sa.Column("scope_type", sa.String(length=20), nullable=False),
        sa.Column("scope_key", sa.String(length=160), nullable=False),
        sa.Column("resolved_at", sa.BigInteger(), nullable=True),
        sa.Column("allowed_next_effect_scope", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(["execution_session_id"], [f"{SCHEMA}.execution_sessions.id"]),
        sa.ForeignKeyConstraint(["correlation_id"], [f"{SCHEMA}.execution_correlations.correlation_id"]),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    for column_name in ("execution_session_id", "correlation_id", "scope_type", "scope_key"):
        op.create_index(
            f"ix_wes_runtime_runtime_holds_{column_name}",
            "runtime_holds",
            [column_name],
            schema=SCHEMA,
        )

    op.create_table(
        "conveyor_queue_memberships",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("bin_code", sa.String(length=100), nullable=True),
        sa.Column("placeholder_key", sa.String(length=240), nullable=True),
        sa.Column("workline_id", sa.Integer(), nullable=False),
        sa.Column("conveyor_code", sa.String(length=80), nullable=False),
        sa.Column("queue_code", sa.String(length=80), nullable=False),
        sa.Column("queue_role", sa.String(length=40), nullable=False),
        sa.Column("membership_status", sa.String(length=20), nullable=False),
        sa.Column("entered_at", sa.BigInteger(), nullable=False),
        sa.Column("left_at", sa.BigInteger(), nullable=True),
        sa.Column("correlation_id", sa.String(length=120), nullable=True),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["correlation_id"], [f"{SCHEMA}.execution_correlations.correlation_id"]),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ux_wes_runtime_conveyor_queue_memberships_active_bin",
        "conveyor_queue_memberships",
        ["workline_id", "bin_code"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("bin_code IS NOT NULL AND membership_status = 'ACTIVE'"),
    )
    op.create_index(
        "ux_wes_runtime_conveyor_queue_memberships_active_placeholder",
        "conveyor_queue_memberships",
        ["workline_id", "placeholder_key"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("placeholder_key IS NOT NULL AND membership_status = 'ACTIVE'"),
    )
    op.create_index(
        "ix_wes_runtime_conveyor_queue_memberships_workline_queue",
        "conveyor_queue_memberships",
        ["workline_id", "queue_code"],
        schema=SCHEMA,
    )
    for column_name in (
        "bin_code",
        "placeholder_key",
        "workline_id",
        "conveyor_code",
        "queue_code",
        "queue_role",
        "membership_status",
        "correlation_id",
    ):
        op.create_index(
            f"ix_wes_runtime_conveyor_queue_memberships_{column_name}",
            "conveyor_queue_memberships",
            [column_name],
            schema=SCHEMA,
        )

    op.create_table(
        "idempotency_keys",
        sa.Column("provider_code", sa.String(length=60), nullable=False),
        sa.Column("operation_kind", sa.String(length=80), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("execution_correlation_id", sa.String(length=120), nullable=False),
        sa.Column("request_hash", sa.String(length=128), nullable=False),
        sa.Column("business_owner_key", sa.String(length=160), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["execution_correlation_id"], [f"{SCHEMA}.execution_correlations.correlation_id"]),
        sa.PrimaryKeyConstraint("provider_code", "operation_kind", "idempotency_key"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wes_runtime_idempotency_keys_execution_correlation_id",
        "idempotency_keys",
        ["execution_correlation_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wes_runtime_idempotency_keys_created_at",
        "idempotency_keys",
        ["created_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_wes_runtime_idempotency_keys_created_at", table_name="idempotency_keys", schema=SCHEMA)
    op.drop_index(
        "ix_wes_runtime_idempotency_keys_execution_correlation_id",
        table_name="idempotency_keys",
        schema=SCHEMA,
    )
    op.drop_table("idempotency_keys", schema=SCHEMA)

    for column_name in (
        "correlation_id",
        "membership_status",
        "queue_role",
        "queue_code",
        "conveyor_code",
        "workline_id",
        "placeholder_key",
        "bin_code",
    ):
        op.drop_index(
            f"ix_wes_runtime_conveyor_queue_memberships_{column_name}",
            table_name="conveyor_queue_memberships",
            schema=SCHEMA,
        )
    op.drop_index(
        "ix_wes_runtime_conveyor_queue_memberships_workline_queue",
        table_name="conveyor_queue_memberships",
        schema=SCHEMA,
    )
    op.drop_index(
        "ux_wes_runtime_conveyor_queue_memberships_active_placeholder",
        table_name="conveyor_queue_memberships",
        schema=SCHEMA,
    )
    op.drop_index(
        "ux_wes_runtime_conveyor_queue_memberships_active_bin",
        table_name="conveyor_queue_memberships",
        schema=SCHEMA,
    )
    op.drop_table("conveyor_queue_memberships", schema=SCHEMA)

    for column_name in ("scope_key", "scope_type", "correlation_id", "execution_session_id"):
        op.drop_index(f"ix_wes_runtime_runtime_holds_{column_name}", table_name="runtime_holds", schema=SCHEMA)
    op.drop_table("runtime_holds", schema=SCHEMA)

    for column_name in ("occurred_at", "event_type", "correlation_id", "trace_id", "execution_session_id"):
        op.drop_index(f"ix_wes_runtime_runtime_timelines_{column_name}", table_name="runtime_timelines", schema=SCHEMA)
    op.drop_table("runtime_timelines", schema=SCHEMA)

    for column_name in (
        "dispatch_status",
        "idempotency_key",
        "provider_code",
        "correlation_id",
        "execution_session_id",
    ):
        op.drop_index(
            f"ix_wes_runtime_runtime_intent_logs_{column_name}",
            table_name="runtime_intent_logs",
            schema=SCHEMA,
        )
    op.drop_table("runtime_intent_logs", schema=SCHEMA)

    for column_name in (
        "status",
        "source_event_id",
        "event_type",
        "provider_code",
        "correlation_id",
        "execution_session_id",
    ):
        op.drop_index(f"ix_wes_runtime_runtime_inbox_{column_name}", table_name="runtime_inbox", schema=SCHEMA)
    op.drop_index("ux_wes_runtime_runtime_inbox_source_event", table_name="runtime_inbox", schema=SCHEMA)
    op.drop_table("runtime_inbox", schema=SCHEMA)

    for column_name in ("parent_correlation_id", "object_key", "execution_session_id"):
        op.drop_index(
            f"ix_wes_runtime_execution_work_items_{column_name}",
            table_name="execution_work_items",
            schema=SCHEMA,
        )
    op.drop_table("execution_work_items", schema=SCHEMA)
