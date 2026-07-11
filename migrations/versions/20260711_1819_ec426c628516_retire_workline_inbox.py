"""retire workline inbox

Revision ID: ec426c628516
Revises: b8a28e1bfec8
Create Date: 2026-07-11 18:19:46.448331+08:00

本迁移保留 workline_runtime_status_projections 与 bin_transit_memberships
的 runtime_status 所有权，不修改两者结构。
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ec426c628516"
down_revision: Union[str, Sequence[str], None] = "b8a28e1bfec8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BIZ_SCHEMA = "wes_biz"
RUNTIME_SCHEMA = "wes_runtime"

_DEPENDENT_FOREIGN_KEYS = (
    (
        "workline_diagnostics",
        "inbox_id",
        "workline_diagnostics_inbox_id_fkey",
        "fk_workline_diagnostics_inbox_id_runtime_inbox",
    ),
    (
        "runtime_holds",
        "source_inbox_id",
        "fk_runtime_holds_source_inbox_id_workline_inbox",
        "fk_runtime_holds_source_inbox_id_runtime_inbox",
    ),
    (
        "smt_inbound_handoff_source_items",
        "source_pick_inbox_id",
        "fk_smt_inbound_handoff_source_items_source_pick_inbox_i_cf89",
        "fk_smt_inbound_handoff_source_items_source_pick_inbox_runtime",
    ),
)


def upgrade() -> None:
    """迁移所有引用并删除旧 Inbox 表。"""
    op.add_column(
        "runtime_inbox",
        sa.Column("workline_session_id", sa.BigInteger(), nullable=True),
        schema=RUNTIME_SCHEMA,
    )
    op.create_index(
        "ix_wes_runtime_runtime_inbox_workline_session_id",
        "runtime_inbox",
        ["workline_session_id"],
        schema=RUNTIME_SCHEMA,
    )
    op.create_foreign_key(
        "fk_runtime_inbox_workline_session_id_workline_sessions",
        "runtime_inbox",
        "workline_sessions",
        ["workline_session_id"],
        ["id"],
        source_schema=RUNTIME_SCHEMA,
        referent_schema=BIZ_SCHEMA,
    )

    # 旧 ID 与 RuntimeInbox ID 不共享命名空间；项目未发布，安全清空而非错误映射。
    op.execute("UPDATE wes_biz.workline_diagnostics SET inbox_id = NULL WHERE inbox_id IS NOT NULL")
    op.execute("UPDATE wes_biz.runtime_holds SET source_inbox_id = NULL WHERE source_inbox_id IS NOT NULL")
    op.execute(
        "UPDATE wes_biz.smt_inbound_handoff_source_items "
        "SET source_pick_inbox_id = NULL WHERE source_pick_inbox_id IS NOT NULL"
    )
    for table_name, column_name, old_constraint, new_constraint in _DEPENDENT_FOREIGN_KEYS:
        op.drop_constraint(old_constraint, table_name, schema=BIZ_SCHEMA, type_="foreignkey")
        op.create_foreign_key(
            new_constraint,
            table_name,
            "runtime_inbox",
            [column_name],
            ["id"],
            source_schema=BIZ_SCHEMA,
            referent_schema=RUNTIME_SCHEMA,
        )

    op.drop_table("workline_inbox", schema=BIZ_SCHEMA)


def downgrade() -> None:
    """重建空旧表并恢复引用；不尝试伪造已清空的旧 ID。"""
    op.create_table(
        "workline_inbox",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column("source_message_id", sa.String(length=200), nullable=True),
        sa.Column("workline_id", sa.Integer(), nullable=True),
        sa.Column("device_id", sa.Integer(), nullable=True),
        sa.Column("command_id", sa.Integer(), nullable=True),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("processor_token", sa.String(length=200), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("source_system", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(), nullable=True),
        sa.Column("trace_id", sa.String(length=100), nullable=True),
        sa.Column("event_id", sa.String(length=200), nullable=True),
        sa.Column("causation_id", sa.String(length=200), nullable=True),
        sa.Column("claim_bucket_key", sa.String(length=200), nullable=False),
        sa.CheckConstraint(
            "kind IN ('DEVICE_EVENT','COMMAND_RESULT','EXTERNAL_HTTP','INTERNAL_EVENT','TIMER_TIMEOUT',"
            "'MANUAL_HOLD','MANUAL_RESUME','MANUAL_CANCEL','REPLAY_REQUEST')",
            name="ck_workline_inbox_inboxkind",
        ),
        sa.CheckConstraint(
            "status IN ('NEW','PROCESSING','PROCESSED','FAILED','RETRY','DEAD_LETTER')",
            name="ck_workline_inbox_inboxstatus",
        ),
        sa.CheckConstraint(
            "source_system IN ('DEVICE','WCS','MES','ERP','MANUAL','SYSTEM')",
            name="ck_workline_inbox_sourcesystem",
        ),
        sa.ForeignKeyConstraint(
            ["command_id"], ["wes_biz.device_commands.id"], name="fk_workline_inbox_command_id_device_commands"
        ),
        sa.ForeignKeyConstraint(["device_id"], ["wes_biz.devices.id"], name="fk_workline_inbox_device_id_devices"),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["wes_biz.workline_sessions.id"],
            name="fk_workline_inbox_session_id_workline_sessions",
        ),
        sa.ForeignKeyConstraint(
            ["workline_id"], ["wes_biz.work_lines.id"], name="fk_workline_inbox_workline_id_work_lines"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workline_inbox"),
        schema=BIZ_SCHEMA,
    )

    for name, columns, unique, predicate in (
        ("ix_wes_biz_workline_inbox_command_id", ["command_id"], False, None),
        ("ix_wes_biz_workline_inbox_device_id", ["device_id"], False, None),
        ("ix_wes_biz_workline_inbox_event_id", ["event_id"], False, None),
        (
            "ix_wes_biz_workline_inbox_hot_claim_bucket_fifo",
            ["claim_bucket_key", "received_at", "id"],
            False,
            "status IN ('NEW', 'RETRY', 'PROCESSING')",
        ),
        ("ix_wes_biz_workline_inbox_id", ["id"], True, None),
        ("ix_wes_biz_workline_inbox_idempotency_key", ["idempotency_key"], False, None),
        ("ix_wes_biz_workline_inbox_kind", ["kind"], False, None),
        ("ix_wes_biz_workline_inbox_new_received_at", ["received_at"], False, "status = 'NEW'"),
        ("ix_wes_biz_workline_inbox_next_retry_at", ["next_retry_at"], False, None),
        (
            "ix_wes_biz_workline_inbox_processing_updated_received_at",
            ["updated_at", "received_at"],
            False,
            "status = 'PROCESSING'",
        ),
        ("ix_wes_biz_workline_inbox_received_at", ["received_at"], False, None),
        (
            "ix_wes_biz_workline_inbox_retry_next_retry_received_at",
            ["next_retry_at", "received_at"],
            False,
            "status = 'RETRY'",
        ),
        ("ix_wes_biz_workline_inbox_session_id", ["session_id"], False, None),
        ("ix_wes_biz_workline_inbox_source_system", ["source_system"], False, None),
        ("ix_wes_biz_workline_inbox_status", ["status"], False, None),
        ("ix_wes_biz_workline_inbox_trace_id", ["trace_id"], False, None),
        ("ix_wes_biz_workline_inbox_workline_id", ["workline_id"], False, None),
        ("uq_workline_inbox_idempotency_key", ["idempotency_key"], True, "idempotency_key IS NOT NULL"),
    ):
        op.create_index(
            name,
            "workline_inbox",
            columns,
            unique=unique,
            schema=BIZ_SCHEMA,
            postgresql_where=sa.text(predicate) if predicate else None,
        )

    # Runtime ID 不能解释为旧表 ID；回切 FK 前保持引用为空。
    op.execute("UPDATE wes_biz.workline_diagnostics SET inbox_id = NULL WHERE inbox_id IS NOT NULL")
    op.execute("UPDATE wes_biz.runtime_holds SET source_inbox_id = NULL WHERE source_inbox_id IS NOT NULL")
    op.execute(
        "UPDATE wes_biz.smt_inbound_handoff_source_items "
        "SET source_pick_inbox_id = NULL WHERE source_pick_inbox_id IS NOT NULL"
    )
    for table_name, column_name, old_constraint, new_constraint in _DEPENDENT_FOREIGN_KEYS:
        op.drop_constraint(new_constraint, table_name, schema=BIZ_SCHEMA, type_="foreignkey")
        op.create_foreign_key(
            old_constraint,
            table_name,
            "workline_inbox",
            [column_name],
            ["id"],
            source_schema=BIZ_SCHEMA,
            referent_schema=BIZ_SCHEMA,
        )

    op.drop_constraint(
        "fk_runtime_inbox_workline_session_id_workline_sessions",
        "runtime_inbox",
        schema=RUNTIME_SCHEMA,
        type_="foreignkey",
    )
    op.drop_index(
        "ix_wes_runtime_runtime_inbox_workline_session_id",
        table_name="runtime_inbox",
        schema=RUNTIME_SCHEMA,
    )
    op.drop_column("runtime_inbox", "workline_session_id", schema=RUNTIME_SCHEMA)
