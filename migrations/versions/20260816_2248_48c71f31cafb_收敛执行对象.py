"""收敛 Phase 8 执行对象

Revision ID: 48c71f31cafb
Revises: ef9495ba331d
Create Date: 2026-08-16 22:48:00.355018+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "48c71f31cafb"
down_revision: Union[str, Sequence[str], None] = "ef9495ba331d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """直接切换到最终 execution owner，不承接开发期 Epoch/evidence 数据。"""

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM wes_biz.line_run_epochs LIMIT 1) THEN
                RAISE EXCEPTION
                    'LineRunEpoch 存在开发期数据；Phase 8 direct cutover 要求清空后重建 Epoch';
            END IF;
        END
        $$
        """
    )
    for name, length in (
        ("plugin_key", 100),
        ("plugin_version", 50),
        ("flow_mode", 100),
    ):
        op.add_column(
            "line_run_epochs",
            sa.Column(name, sa.String(length=length), nullable=False),
            schema="wes_biz",
        )

    op.create_table(
        "material_executions",
        *_enterprise_columns(),
        sa.Column("execution_code", sa.String(length=120), nullable=False),
        sa.Column("material_trace_id", sa.String(length=160), nullable=False),
        sa.Column("workline_id", sa.Integer(), nullable=False),
        sa.Column("line_run_epoch_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("last_transition_reason", sa.String(length=120), nullable=False),
        sa.Column("last_transition_evidence_id", sa.Integer(), nullable=False),
        sa.Column("status_changed_at", sa.DateTime(), nullable=False),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('CREATED', 'RUNNING', 'HOLD', 'CLOSED', 'RECONCILING')",
            name="material_execution_status_valid",
        ),
        sa.ForeignKeyConstraint(["workline_id"], ["wes_biz.work_lines.id"]),
        sa.ForeignKeyConstraint(["line_run_epoch_id"], ["wes_biz.line_run_epochs.id"]),
        sa.UniqueConstraint("execution_code", name="ux_material_executions_execution_code"),
        schema="wes_biz",
    )
    op.create_index(
        "ux_material_executions_active_trace",
        "material_executions",
        ["material_trace_id"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("status <> 'CLOSED'"),
    )
    op.create_index(
        "ix_material_executions_epoch_status",
        "material_executions",
        ["line_run_epoch_id", "status", "id"],
        schema="wes_biz",
    )
    for column in (
        "material_trace_id",
        "workline_id",
        "line_run_epoch_id",
        "status",
        "last_transition_evidence_id",
    ):
        op.create_index(
            op.f(f"ix_wes_biz_material_executions_{column}"),
            "material_executions",
            [column],
            schema="wes_biz",
        )

    op.create_table(
        "inbound_evidences",
        *_enterprise_columns(),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("source_identity", sa.String(length=300), nullable=False),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column("normalized_payload", sa.JSON(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("line_run_epoch_id", sa.Integer(), nullable=True),
        sa.Column("material_execution_id", sa.Integer(), nullable=True),
        sa.Column("device_code", sa.String(length=100), nullable=True),
        sa.Column("command_code", sa.String(length=100), nullable=True),
        sa.Column("contract_key", sa.String(length=100), nullable=True),
        sa.Column("contract_version", sa.String(length=50), nullable=True),
        sa.Column("operation", sa.String(length=160), nullable=True),
        sa.Column("operation_id", sa.String(length=160), nullable=True),
        sa.Column("apply_status", sa.String(length=20), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('DEVICE_EVENT', 'DEVICE_RESULT', 'WMS_EVENT', 'WMS_RESULT')",
            name="inbound_evidence_kind_valid",
        ),
        sa.CheckConstraint(
            "apply_status IN ('PENDING', 'APPLIED', 'IGNORED', 'RECONCILING')",
            name="inbound_evidence_apply_status_valid",
        ),
        sa.CheckConstraint(
            "kind NOT IN ('WMS_EVENT', 'WMS_RESULT') OR (operation IS NOT NULL AND operation_id IS NOT NULL)",
            name="inbound_evidence_wms_identity_required",
        ),
        sa.CheckConstraint(
            "kind NOT IN ('DEVICE_EVENT', 'DEVICE_RESULT') OR device_code IS NOT NULL",
            name="inbound_evidence_device_identity_required",
        ),
        sa.ForeignKeyConstraint(["line_run_epoch_id"], ["wes_biz.line_run_epochs.id"]),
        sa.ForeignKeyConstraint(["material_execution_id"], ["wes_biz.material_executions.id"]),
        sa.UniqueConstraint("source_identity", name="ux_inbound_evidences_source_identity"),
        schema="wes_biz",
    )
    op.create_index(
        "ix_inbound_evidences_pending",
        "inbound_evidences",
        ["received_at", "id"],
        schema="wes_biz",
        postgresql_where=sa.text("apply_status = 'PENDING'"),
    )
    op.create_index(
        "ix_inbound_evidences_device_command",
        "inbound_evidences",
        ["device_code", "command_code", "kind"],
        schema="wes_biz",
    )
    op.create_index(
        "ux_inbound_evidences_device_result",
        "inbound_evidences",
        ["command_code"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("kind = 'DEVICE_RESULT' AND command_code IS NOT NULL"),
    )
    op.create_index(
        "ux_inbound_evidences_wms_identity",
        "inbound_evidences",
        ["operation", "operation_id"],
        unique=True,
        schema="wes_biz",
        postgresql_where=sa.text("kind IN ('WMS_EVENT', 'WMS_RESULT')"),
    )
    for column in (
        "line_run_epoch_id",
        "material_execution_id",
        "device_code",
        "command_code",
        "operation",
        "operation_id",
        "apply_status",
    ):
        op.create_index(
            op.f(f"ix_wes_biz_inbound_evidences_{column}"),
            "inbound_evidences",
            [column],
            schema="wes_biz",
        )

    op.create_foreign_key(
        "fk_material_executions_last_transition_evidence_id",
        "material_executions",
        "inbound_evidences",
        ["last_transition_evidence_id"],
        ["id"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )

    op.create_table(
        "inbound_evidence_conflicts",
        *_enterprise_columns(),
        sa.Column("source_identity", sa.String(length=300), nullable=False),
        sa.Column("first_evidence_id", sa.Integer(), nullable=False),
        sa.Column("conflicting_digest", sa.String(length=64), nullable=False),
        sa.Column("normalized_payload", sa.JSON(), nullable=False),
        sa.Column("reason_code", sa.String(length=120), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["first_evidence_id"], ["wes_biz.inbound_evidences.id"]),
        schema="wes_biz",
    )
    op.create_index(
        "ix_inbound_evidence_conflicts_source_received",
        "inbound_evidence_conflicts",
        ["source_identity", "received_at", "id"],
        schema="wes_biz",
    )
    op.create_index(
        op.f("ix_wes_biz_inbound_evidence_conflicts_first_evidence_id"),
        "inbound_evidence_conflicts",
        ["first_evidence_id"],
        schema="wes_biz",
    )

    op.create_table(
        "wms_confirmations",
        *_enterprise_columns(),
        sa.Column("operation", sa.String(length=160), nullable=False),
        sa.Column("operation_id", sa.String(length=160), nullable=False),
        sa.Column("material_execution_id", sa.Integer(), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("deadline_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("retry_eligible", sa.Boolean(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("claim_token", sa.String(length=80), nullable=True),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(), nullable=True),
        sa.Column("last_dispatch_at", sa.DateTime(), nullable=True),
        sa.Column("response_evidence_id", sa.Integer(), nullable=True),
        sa.Column("response_result", sa.String(length=80), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING', 'DISPATCHING', 'COMPLETED', 'RECONCILING')",
            name="wms_confirmation_status_valid",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="wms_confirmation_attempt_count_nonnegative"),
        sa.ForeignKeyConstraint(["material_execution_id"], ["wes_biz.material_executions.id"]),
        sa.ForeignKeyConstraint(["response_evidence_id"], ["wes_biz.inbound_evidences.id"]),
        sa.UniqueConstraint("operation", "operation_id", name="ux_wms_confirmations_operation_identity"),
        schema="wes_biz",
    )
    op.create_index(
        "ix_wms_confirmations_dispatch_eligible",
        "wms_confirmations",
        ["status", "retry_eligible", "next_attempt_at", "id"],
        schema="wes_biz",
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    for column in ("material_execution_id", "status", "response_evidence_id"):
        op.create_index(
            op.f(f"ix_wes_biz_wms_confirmations_{column}"),
            "wms_confirmations",
            [column],
            schema="wes_biz",
        )

    op.drop_constraint(
        "fk_device_commands_result_evidence_id",
        "device_commands",
        schema="wes_biz",
        type_="foreignkey",
    )
    op.execute(
        'UPDATE "wes_biz"."device_commands" SET "result_evidence_id" = NULL WHERE "result_evidence_id" IS NOT NULL'
    )
    op.drop_table("device_evidence_conflicts", schema="wes_biz")
    op.drop_table("device_evidences", schema="wes_biz")
    op.create_foreign_key(
        "fk_device_commands_result_evidence_id",
        "device_commands",
        "inbound_evidences",
        ["result_evidence_id"],
        ["id"],
        source_schema="wes_biz",
        referent_schema="wes_biz",
    )
    op.execute(
        'ALTER TABLE "wes_biz"."resource_bin_material_mounts" DROP COLUMN IF EXISTS "wms_confirmation_status" CASCADE'
    )


def downgrade() -> None:
    raise NotImplementedError("Phase 8 direct cutover 不支持恢复已删除的开发期 owner")


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
