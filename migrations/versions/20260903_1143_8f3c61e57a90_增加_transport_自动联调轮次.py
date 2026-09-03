"""增加 transport 自动联调轮次

Revision ID: 8f3c61e57a90
Revises: ed5ed8eb0c46
Create Date: 2026-09-03 11:43:18.686263+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8f3c61e57a90"
down_revision: Union[str, Sequence[str], None] = "ed5ed8eb0c46"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "transport_callback_receipts",
        sa.Column("conflict_code", sa.String(length=120), nullable=True),
        schema="wes_runtime",
    )
    op.add_column(
        "transport_callback_receipts",
        sa.Column("conflict_detected_at", sa.DateTime(), nullable=True),
        schema="wes_runtime",
    )
    op.create_check_constraint(
        op.f("ck_transport_callback_receipts_transport_callback_receipt_conflict_complete"),
        "transport_callback_receipts",
        "(conflict_code IS NULL) = (conflict_detected_at IS NULL)",
        schema="wes_runtime",
    )
    op.create_index(
        "ix_inbound_evidences_device_event_range",
        "inbound_evidences",
        ["received_at", "id"],
        unique=False,
        schema="wes_biz",
        postgresql_where=sa.text("kind = 'DEVICE_EVENT'"),
    )
    op.create_table(
        "transport_debug_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("active_scope", sa.String(length=20), nullable=True),
        sa.Column("rack_id", sa.String(length=100), nullable=False),
        sa.Column("configuration_json", sa.JSON(), nullable=False),
        sa.Column("current_group_index", sa.Integer(), nullable=False),
        sa.Column("current_phase", sa.String(length=40), nullable=False),
        sa.Column("current_step_ordinal", sa.Integer(), nullable=False),
        sa.Column("attention_code", sa.String(length=120), nullable=True),
        sa.Column("attention_detail", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("claim_token", sa.String(length=80), nullable=True),
        sa.Column("claim_until", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("aborted_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("aborted_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'NEEDS_ATTENTION', 'COMPLETED', 'FAILED', 'ABORTED')",
            name=op.f("ck_transport_debug_runs_transport_debug_run_status_valid"),
        ),
        sa.CheckConstraint(
            "current_phase IN ('RACK_TO_STATION', 'BINS_TO_INFEED', 'WAIT_SCAN12', 'BINS_TO_RACK', "
            "'ROTATE_TO_NEXT_FACE', 'RACK_TO_STORAGE')",
            name=op.f("ck_transport_debug_runs_transport_debug_run_phase_valid"),
        ),
        sa.CheckConstraint(
            "(status IN ('RUNNING', 'NEEDS_ATTENTION') AND active_scope IS NOT NULL "
            "AND active_scope = 'GLOBAL') OR "
            "(status IN ('COMPLETED', 'FAILED', 'ABORTED') AND active_scope IS NULL)",
            name=op.f("ck_transport_debug_runs_transport_debug_run_status_scope_consistent"),
        ),
        sa.CheckConstraint(
            "(claim_token IS NULL) = (claim_until IS NULL)",
            name=op.f("ck_transport_debug_runs_transport_debug_run_claim_complete"),
        ),
        sa.CheckConstraint(
            "claim_token IS NULL OR (active_scope IS NOT NULL AND active_scope = 'GLOBAL')",
            name=op.f("ck_transport_debug_runs_transport_debug_run_claim_requires_active_scope"),
        ),
        sa.CheckConstraint(
            "current_group_index >= 0 AND current_step_ordinal >= 0 AND version > 0",
            name=op.f("ck_transport_debug_runs_transport_debug_run_cursor_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transport_debug_runs")),
        sa.UniqueConstraint("run_id", name="ux_transport_debug_runs_run_id"),
        sa.UniqueConstraint("active_scope", name="ux_transport_debug_runs_active_scope"),
        schema="wes_runtime",
    )
    op.create_index(
        "ix_transport_debug_runs_claim",
        "transport_debug_runs",
        ["claim_until", "id"],
        unique=False,
        schema="wes_runtime",
        postgresql_where=sa.text("active_scope = 'GLOBAL'"),
    )
    op.create_index(
        "ix_transport_debug_runs_recent",
        "transport_debug_runs",
        ["created_at", "id"],
        unique=False,
        schema="wes_runtime",
    )
    op.create_table(
        "transport_debug_run_steps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=80), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("group_index", sa.Integer(), nullable=True),
        sa.Column("phase", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("client_request_id", sa.String(length=120), nullable=True),
        sa.Column("transport_task_id", sa.String(length=80), nullable=True),
        sa.Column("evidence_high_watermark", sa.BigInteger(), nullable=True),
        sa.Column("evidence_not_before_ms", sa.BigInteger(), nullable=True),
        sa.Column("observed_bins_json", sa.JSON(), nullable=False),
        sa.Column("reason_code", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING', 'WAITING', 'SUCCEEDED', 'FAILED', 'NEEDS_ATTENTION')",
            name=op.f("ck_transport_debug_run_steps_transport_debug_run_step_status_valid"),
        ),
        sa.CheckConstraint(
            "phase IN ('RACK_TO_STATION', 'BINS_TO_INFEED', 'WAIT_SCAN12', 'BINS_TO_RACK', "
            "'ROTATE_TO_NEXT_FACE', 'RACK_TO_STORAGE')",
            name=op.f("ck_transport_debug_run_steps_transport_debug_run_step_phase_valid"),
        ),
        sa.CheckConstraint(
            "ordinal >= 0 AND (group_index IS NULL OR group_index >= 0)",
            name=op.f("ck_transport_debug_run_steps_transport_debug_run_step_cursor_valid"),
        ),
        sa.CheckConstraint(
            "evidence_high_watermark IS NULL OR evidence_high_watermark >= 0",
            name=op.f("ck_transport_debug_run_steps_transport_debug_run_step_high_watermark_valid"),
        ),
        sa.CheckConstraint(
            "evidence_not_before_ms IS NULL OR evidence_not_before_ms > 0",
            name=op.f("ck_transport_debug_run_steps_transport_debug_run_step_not_before_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["wes_runtime.transport_debug_runs.run_id"],
            name=op.f("fk_transport_debug_run_steps_run_id_transport_debug_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transport_debug_run_steps")),
        sa.UniqueConstraint(
            "run_id",
            "ordinal",
            name="ux_transport_debug_run_steps_run_ordinal",
        ),
        sa.UniqueConstraint(
            "client_request_id",
            name="ux_transport_debug_run_steps_client_request_id",
        ),
        schema="wes_runtime",
    )
    op.create_index(
        "ix_transport_debug_run_steps_run_status",
        "transport_debug_run_steps",
        ["run_id", "status", "ordinal"],
        unique=False,
        schema="wes_runtime",
    )
    op.create_index(
        "ix_transport_debug_run_steps_transport_task",
        "transport_debug_run_steps",
        ["transport_task_id"],
        unique=False,
        schema="wes_runtime",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_transport_debug_run_steps_transport_task",
        table_name="transport_debug_run_steps",
        schema="wes_runtime",
    )
    op.drop_index(
        "ix_transport_debug_run_steps_run_status",
        table_name="transport_debug_run_steps",
        schema="wes_runtime",
    )
    op.drop_table("transport_debug_run_steps", schema="wes_runtime")
    op.drop_index(
        "ix_transport_debug_runs_recent",
        table_name="transport_debug_runs",
        schema="wes_runtime",
    )
    op.drop_index(
        "ix_transport_debug_runs_claim",
        table_name="transport_debug_runs",
        schema="wes_runtime",
    )
    op.drop_table("transport_debug_runs", schema="wes_runtime")
    op.drop_index(
        "ix_inbound_evidences_device_event_range",
        table_name="inbound_evidences",
        schema="wes_biz",
    )
    op.drop_constraint(
        op.f("ck_transport_callback_receipts_transport_callback_receipt_conflict_complete"),
        "transport_callback_receipts",
        schema="wes_runtime",
        type_="check",
    )
    op.drop_column("transport_callback_receipts", "conflict_detected_at", schema="wes_runtime")
    op.drop_column("transport_callback_receipts", "conflict_code", schema="wes_runtime")
