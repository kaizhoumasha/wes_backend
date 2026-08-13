"""rebuild device command ecs lifecycle

Revision ID: a08d72f135d2
Revises: de392f5ff5d0
Create Date: 2026-08-13 10:16:09.061838+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a08d72f135d2"
down_revision: Union[str, Sequence[str], None] = "de392f5ff5d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """直接建立最终 DeviceCommand/ECS schema，不迁移未发布数据。"""

    op.execute("DELETE FROM wes_runtime.runtime_inbox WHERE kind IN ('COMMAND_RESULT', 'DEVICE_EVENT')")
    op.drop_constraint(
        op.f("ck_runtime_inbox_kind_valid"),
        "runtime_inbox",
        schema="wes_runtime",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_runtime_inbox_kind_valid"),
        "runtime_inbox",
        "kind IN ('EXTERNAL_HTTP', 'INTERNAL_EVENT', 'TIMER_TIMEOUT', 'REPLAY_REQUEST')",
        schema="wes_runtime",
    )

    op.execute('DROP TABLE IF EXISTS "wes_biz"."device_runtime_projections" CASCADE')
    op.execute('DROP TABLE IF EXISTS "wes_biz"."device_commands" CASCADE')
    op.execute('DROP INDEX IF EXISTS "wes_biz"."ix_system_outbox_blocked_release"')
    for column in (
        "device_id",
        "blocked_device_id",
        "blocked_at",
        "last_blocked_check_at",
        "blocked_check_count",
        "blocked_detail_json",
    ):
        op.execute(f'ALTER TABLE "wes_biz"."system_outbox" DROP COLUMN IF EXISTS "{column}" CASCADE')
    op.create_index(
        "ix_system_outbox_blocked_release",
        "system_outbox",
        ["blocked_reason", "blocked_workline_id"],
        schema="wes_biz",
    )

    for column in (
        "vendor_type",
        "capabilities_json",
        "host",
        "port",
        "protocol",
        "auth_token",
        "timeout",
        "callback_path",
        "device_status",
        "current_command_id",
        "last_heartbeat_at",
        "error_code",
        "maintenance_mode",
        "max_concurrent_tasks",
        "idempotency_ttl",
    ):
        op.execute(f'ALTER TABLE "wes_biz"."devices" DROP COLUMN IF EXISTS "{column}" CASCADE')
    op.alter_column("devices", "device_code", schema="wes_biz", type_=sa.String(length=100))

    op.create_table(
        "line_run_epochs",
        *_enterprise_columns(),
        sa.Column("epoch_code", sa.String(length=100), nullable=False),
        sa.Column("workline_id", sa.Integer(), nullable=False),
        sa.Column("topology_digest", sa.String(length=64), nullable=False),
        sa.Column("configuration_digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("status IN ('ACTIVE', 'CLOSED')", name="line_run_epoch_status_valid"),
        sa.ForeignKeyConstraint(["workline_id"], ["wes_biz.work_lines.id"]),
        sa.UniqueConstraint("epoch_code", name="ux_line_run_epochs_epoch_code"),
        schema="wes_biz",
    )
    op.create_index(
        "ux_line_run_epochs_active_workline",
        "line_run_epochs",
        ["workline_id"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_index(
        op.f("ix_wes_biz_line_run_epochs_workline_id"),
        "line_run_epochs",
        ["workline_id"],
        schema="wes_biz",
    )

    op.create_table(
        "line_run_epoch_device_bindings",
        *_enterprise_columns(),
        sa.Column("line_run_epoch_id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("device_code", sa.String(length=100), nullable=False),
        sa.Column("contract_key", sa.String(length=100), nullable=False),
        sa.Column("contract_version", sa.String(length=50), nullable=False),
        sa.Column("status_max_age_ms", sa.Integer(), nullable=False),
        sa.Column("command_timeout_ms", sa.Integer(), nullable=False),
        sa.CheckConstraint("status_max_age_ms > 0", name="line_run_epoch_binding_status_age_positive"),
        sa.CheckConstraint("command_timeout_ms > 0", name="line_run_epoch_binding_timeout_positive"),
        sa.ForeignKeyConstraint(["line_run_epoch_id"], ["wes_biz.line_run_epochs.id"]),
        sa.ForeignKeyConstraint(["device_id"], ["wes_biz.devices.id"]),
        sa.UniqueConstraint(
            "line_run_epoch_id",
            "device_code",
            name="ux_line_run_epoch_device_bindings_epoch_device_code",
        ),
        sa.UniqueConstraint(
            "line_run_epoch_id",
            "device_id",
            name="ux_line_run_epoch_device_bindings_epoch_device_id",
        ),
        schema="wes_biz",
    )
    for column in ("line_run_epoch_id", "device_id"):
        op.create_index(
            op.f(f"ix_wes_biz_line_run_epoch_device_bindings_{column}"),
            "line_run_epoch_device_bindings",
            [column],
            schema="wes_biz",
        )

    op.create_table(
        "device_commands",
        *_enterprise_columns(),
        sa.Column("device_code", sa.String(length=100), nullable=False),
        sa.Column("line_run_epoch_id", sa.Integer(), nullable=False),
        sa.Column("execution_ref_type", sa.String(length=50), nullable=False),
        sa.Column("execution_ref_id", sa.String(length=120), nullable=False),
        sa.Column("contract_key", sa.String(length=100), nullable=False),
        sa.Column("contract_version", sa.String(length=50), nullable=False),
        sa.Column("task_type", sa.String(length=100), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("deadline_at", sa.DateTime(), nullable=False),
        sa.Column("trace_id", sa.String(length=100), nullable=True),
        sa.Column("command_code", sa.String(length=100), nullable=False),
        sa.Column("device_binding_id", sa.Integer(), nullable=False),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("claim_token", sa.String(length=80), nullable=True),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(), nullable=True),
        sa.Column("ack_received_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("result_evidence_id", sa.Integer(), nullable=True),
        sa.Column("failure_code", sa.String(length=120), nullable=True),
        sa.Column("reconciliation_reason", sa.String(length=120), nullable=True),
        sa.Column("outcome_published_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING', 'DISPATCHING', 'ACKNOWLEDGED', 'RECONCILING', 'SUCCEEDED', 'FAILED', 'TIMED_OUT')",
            name="device_command_status_valid",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="device_command_attempt_count_nonnegative"),
        sa.ForeignKeyConstraint(["line_run_epoch_id"], ["wes_biz.line_run_epochs.id"]),
        sa.ForeignKeyConstraint(
            ["device_binding_id"],
            ["wes_biz.line_run_epoch_device_bindings.id"],
        ),
        sa.UniqueConstraint("command_code", name="ux_device_commands_command_code"),
        sa.UniqueConstraint(
            "line_run_epoch_id",
            "device_code",
            "execution_ref_type",
            "execution_ref_id",
            name="ux_device_commands_execution_identity",
        ),
        schema="wes_biz",
    )
    op.create_index(
        "ux_device_commands_unclosed_device",
        "device_commands",
        ["device_code"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("status IN ('PENDING', 'DISPATCHING', 'ACKNOWLEDGED', 'RECONCILING')"),
    )
    op.create_index(
        "ix_device_commands_dispatch_claim",
        "device_commands",
        ["status", "next_attempt_at", "id"],
        schema="wes_biz",
    )
    for column in ("device_binding_id", "result_evidence_id", "status"):
        op.create_index(
            op.f(f"ix_wes_biz_device_commands_{column}"),
            "device_commands",
            [column],
            schema="wes_biz",
        )

    op.create_table(
        "device_status_observations",
        *_enterprise_columns(),
        sa.Column("device_code", sa.String(length=100), nullable=False),
        sa.Column("command_code", sa.String(length=100), nullable=True),
        sa.Column("contract_key", sa.String(length=100), nullable=False),
        sa.Column("contract_version", sa.String(length=50), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("current_command_code", sa.String(length=100), nullable=True),
        sa.Column("device_timestamp", sa.BigInteger(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        schema="wes_biz",
    )
    op.create_index(
        "ix_device_status_observations_device_received",
        "device_status_observations",
        ["device_code", "received_at", "id"],
        schema="wes_biz",
    )

    op.create_table(
        "device_evidences",
        *_enterprise_columns(),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("source_event_id", sa.String(length=160), nullable=False),
        sa.Column("device_code", sa.String(length=100), nullable=False),
        sa.Column("command_code", sa.String(length=100), nullable=True),
        sa.Column("contract_key", sa.String(length=100), nullable=False),
        sa.Column("contract_version", sa.String(length=50), nullable=False),
        sa.Column("line_run_epoch_id", sa.Integer(), nullable=True),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("apply_status", sa.String(length=20), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("kind IN ('RESULT', 'EVENT')", name="device_evidence_kind_valid"),
        sa.CheckConstraint(
            "apply_status IN ('PENDING', 'APPLIED', 'IGNORED', 'RECONCILING')",
            name="device_evidence_apply_status_valid",
        ),
        sa.ForeignKeyConstraint(["line_run_epoch_id"], ["wes_biz.line_run_epochs.id"]),
        sa.UniqueConstraint("source_event_id", name="ux_device_evidences_source_event_id"),
        schema="wes_biz",
    )
    op.create_index(
        "ix_device_evidences_pending",
        "device_evidences",
        ["received_at", "id"],
        schema="wes_biz",
        postgresql_where=sa.text("apply_status = 'PENDING'"),
    )
    op.create_index(
        "ix_device_evidences_command",
        "device_evidences",
        ["command_code", "kind"],
        schema="wes_biz",
    )
    op.create_index(
        "ux_device_evidences_command_result",
        "device_evidences",
        ["command_code"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("kind = 'RESULT' AND command_code IS NOT NULL"),
    )
    op.create_index(
        op.f("ix_wes_biz_device_evidences_line_run_epoch_id"),
        "device_evidences",
        ["line_run_epoch_id"],
        schema="wes_biz",
    )

    op.create_table(
        "device_evidence_conflicts",
        *_enterprise_columns(),
        sa.Column("source_event_id", sa.String(length=160), nullable=False),
        sa.Column("first_evidence_id", sa.Integer(), nullable=False),
        sa.Column("conflicting_digest", sa.String(length=64), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("reason_code", sa.String(length=120), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["first_evidence_id"], ["wes_biz.device_evidences.id"]),
        schema="wes_biz",
    )
    op.create_index(
        "ix_device_evidence_conflicts_source_received",
        "device_evidence_conflicts",
        ["source_event_id", "received_at", "id"],
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_device_evidence_conflicts_first_evidence_id"),
        "device_evidence_conflicts",
        ["first_evidence_id"],
        schema="wes_biz",
    )
    op.create_foreign_key(
        "fk_device_commands_result_evidence_id",
        "device_commands",
        "device_evidences",
        ["result_evidence_id"],
        ["id"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )


def downgrade() -> None:
    raise NotImplementedError("Phase 7 不提供已退役设备执行 schema 的 downgrade")


def _enterprise_columns() -> tuple[sa.Column[object], ...]:
    return (
        sa.Column("version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
