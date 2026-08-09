"""新增 AGV CTU 通用搬运聚合

Revision ID: a8d9b9eba49b
Revises: 7fadfb5469ee
Create Date: 2026-08-09 20:29:54.627652+08:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a8d9b9eba49b"
down_revision: Union[str, Sequence[str], None] = "7fadfb5469ee"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """创建 Phase 4 Transport 聚合表和领取索引。"""

    op.create_table(
        "transport_tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("transport_task_id", sa.String(length=80), nullable=False),
        sa.Column("client_request_id", sa.String(length=120), nullable=False),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("caller_json", sa.JSON(), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reason_code", sa.String(length=120), nullable=True),
        sa.Column("submit_attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_submit_at", sa.DateTime(), nullable=True),
        sa.Column("send_started_at", sa.DateTime(), nullable=True),
        sa.Column("result_deadline_at", sa.DateTime(), nullable=True),
        sa.Column("submit_claim_token", sa.String(length=80), nullable=True),
        sa.Column("submit_claim_until", sa.DateTime(), nullable=True),
        sa.Column("outcome_version", sa.Integer(), nullable=False),
        sa.Column("published_outcome_version", sa.Integer(), nullable=False),
        sa.Column("outcome_json", sa.JSON(), nullable=True),
        sa.Column("outcome_claim_token", sa.String(length=80), nullable=True),
        sa.Column("outcome_claim_until", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING', 'ACCEPTED', 'REJECTED', 'SUCCEEDED', 'FAILED', 'RECONCILING')",
            name="transport_task_status_valid",
        ),
        sa.CheckConstraint(
            "submit_attempt_count BETWEEN 0 AND 3",
            name="transport_submit_attempt_count_valid",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transport_tasks")),
        sa.UniqueConstraint("client_request_id", name="ux_transport_tasks_client_request_id"),
        sa.UniqueConstraint("transport_task_id", name="ux_transport_tasks_task_id"),
        schema="wes_runtime",
    )
    op.create_index(
        "ix_transport_tasks_submit_claim",
        "transport_tasks",
        ["status", "next_submit_at", "id"],
        schema="wes_runtime",
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.create_index(
        "ix_transport_tasks_result_deadline",
        "transport_tasks",
        ["result_deadline_at", "id"],
        schema="wes_runtime",
        postgresql_where=sa.text("status = 'ACCEPTED' AND result_deadline_at IS NOT NULL"),
    )
    op.create_index(
        "ix_transport_tasks_ambiguous_claim",
        "transport_tasks",
        ["submit_claim_until", "id"],
        schema="wes_runtime",
        postgresql_where=sa.text("status = 'PENDING' AND send_started_at IS NOT NULL"),
    )
    op.create_index(
        "ix_transport_tasks_outcome_claim",
        "transport_tasks",
        ["updated_at", "id"],
        schema="wes_runtime",
        postgresql_where=sa.text("outcome_version > published_outcome_version"),
    )

    op.create_table(
        "transport_members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("transport_task_id", sa.String(length=80), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("object_type", sa.String(length=10), nullable=False),
        sa.Column("object_id", sa.String(length=100), nullable=False),
        sa.Column("source_json", sa.JSON(), nullable=False),
        sa.Column("target_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("final_position_json", sa.JSON(), nullable=True),
        sa.Column("position_unknown", sa.Boolean(), nullable=False),
        sa.Column("failure_code", sa.String(length=120), nullable=True),
        sa.Column("arrival_face", sa.String(length=1), nullable=True),
        sa.Column("last_event_id", sa.String(length=120), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["transport_task_id"],
            ["wes_runtime.transport_tasks.transport_task_id"],
            name=op.f("fk_transport_members_transport_task_id_transport_tasks"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transport_members")),
        sa.UniqueConstraint("transport_task_id", "object_id", name="ux_transport_members_task_object"),
        sa.UniqueConstraint("transport_task_id", "ordinal", name="ux_transport_members_task_ordinal"),
        schema="wes_runtime",
    )
    op.create_index(
        op.f("ix_wes_runtime_transport_members_transport_task_id"),
        "transport_members",
        ["transport_task_id"],
        schema="wes_runtime",
    )

    op.create_table(
        "transport_evidence",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.String(length=120), nullable=False),
        sa.Column("transport_task_id", sa.String(length=80), nullable=False),
        sa.Column("operation", sa.String(length=80), nullable=False),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("claim_token", sa.String(length=80), nullable=True),
        sa.Column("claim_until", sa.DateTime(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("conflict_code", sa.String(length=120), nullable=True),
        sa.CheckConstraint("status IN ('PENDING', 'APPLIED', 'CONFLICT')", name="transport_evidence_status_valid"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transport_evidence")),
        sa.UniqueConstraint("event_id", name="ux_transport_evidence_event_id"),
        schema="wes_runtime",
    )
    op.create_index(
        op.f("ix_wes_runtime_transport_evidence_transport_task_id"),
        "transport_evidence",
        ["transport_task_id"],
        schema="wes_runtime",
    )
    op.create_index(
        "ix_transport_evidence_pending_claim",
        "transport_evidence",
        ["status", "received_at", "id"],
        schema="wes_runtime",
        postgresql_where=sa.text("status = 'PENDING'"),
    )

    op.create_table(
        "transport_position_projections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("object_type", sa.String(length=10), nullable=False),
        sa.Column("object_id", sa.String(length=100), nullable=False),
        sa.Column("position_json", sa.JSON(), nullable=True),
        sa.Column("position_unknown", sa.Boolean(), nullable=False),
        sa.Column("arrival_face", sa.String(length=1), nullable=True),
        sa.Column("source_event_id", sa.String(length=120), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transport_position_projections")),
        sa.UniqueConstraint("object_type", "object_id", name="ux_transport_position_projection_object"),
        schema="wes_runtime",
    )

    op.create_table(
        "transport_resource_bindings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("transport_task_id", sa.String(length=80), nullable=False),
        sa.Column("resource_type", sa.String(length=10), nullable=False),
        sa.Column("resource_id", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("released_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["transport_task_id"],
            ["wes_runtime.transport_tasks.transport_task_id"],
            name=op.f("fk_transport_resource_bindings_transport_task_id_transport_tasks"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transport_resource_bindings")),
        schema="wes_runtime",
    )
    op.create_index(
        "ix_transport_resource_bindings_task",
        "transport_resource_bindings",
        ["transport_task_id", "released_at"],
        schema="wes_runtime",
    )
    op.create_index(
        "ux_transport_resource_bindings_active",
        "transport_resource_bindings",
        ["resource_type", "resource_id"],
        unique=True,
        schema="wes_runtime",
        postgresql_where=sa.text("released_at IS NULL"),
    )


def downgrade() -> None:
    """移除 Phase 4 Transport 聚合表。"""

    op.drop_index(
        "ux_transport_resource_bindings_active", table_name="transport_resource_bindings", schema="wes_runtime"
    )
    op.drop_index("ix_transport_resource_bindings_task", table_name="transport_resource_bindings", schema="wes_runtime")
    op.drop_table("transport_resource_bindings", schema="wes_runtime")
    op.drop_table("transport_position_projections", schema="wes_runtime")
    op.drop_index("ix_transport_evidence_pending_claim", table_name="transport_evidence", schema="wes_runtime")
    op.drop_index(
        op.f("ix_wes_runtime_transport_evidence_transport_task_id"),
        table_name="transport_evidence",
        schema="wes_runtime",
    )
    op.drop_table("transport_evidence", schema="wes_runtime")
    op.drop_index(
        op.f("ix_wes_runtime_transport_members_transport_task_id"),
        table_name="transport_members",
        schema="wes_runtime",
    )
    op.drop_table("transport_members", schema="wes_runtime")
    op.drop_index("ix_transport_tasks_outcome_claim", table_name="transport_tasks", schema="wes_runtime")
    op.drop_index("ix_transport_tasks_ambiguous_claim", table_name="transport_tasks", schema="wes_runtime")
    op.drop_index("ix_transport_tasks_result_deadline", table_name="transport_tasks", schema="wes_runtime")
    op.drop_index("ix_transport_tasks_submit_claim", table_name="transport_tasks", schema="wes_runtime")
    op.drop_table("transport_tasks", schema="wes_runtime")
