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
PRE_CUTOVER_AUDIT_ONLY = "PRE_CUTOVER_AUDIT_ONLY"
LEGACY_PROVIDER_CODE = "LEGACY_WORKLINE_INBOX"
LEGACY_EVENT_TYPE = "PRE_CUTOVER_AUDIT_ONLY"

_DEPENDENT_FOREIGN_KEYS = (
    (
        "workline_diagnostics",
        "inbox_id",
        "workline_diagnostics_inbox_id_fkey",
        "fk_workline_diagnostics_inbox_id_runtime_inbox",
        """
        UPDATE wes_biz.workline_diagnostics AS dependent
        SET inbox_id = runtime.id
        FROM wes_runtime.runtime_inbox AS runtime
        WHERE dependent.inbox_id IS NOT NULL
          AND runtime.provider_code = :provider_code
          AND runtime.event_type = :event_type
          AND runtime.source_event_id = 'legacy-workline-inbox:' || dependent.inbox_id::text
        """,
    ),
    (
        "runtime_holds",
        "source_inbox_id",
        "fk_runtime_holds_source_inbox_id_workline_inbox",
        "fk_runtime_holds_source_inbox_id_runtime_inbox",
        """
        UPDATE wes_biz.runtime_holds AS dependent
        SET source_inbox_id = runtime.id
        FROM wes_runtime.runtime_inbox AS runtime
        WHERE dependent.source_inbox_id IS NOT NULL
          AND runtime.provider_code = :provider_code
          AND runtime.event_type = :event_type
          AND runtime.source_event_id = 'legacy-workline-inbox:' || dependent.source_inbox_id::text
        """,
    ),
    (
        "smt_inbound_handoff_source_items",
        "source_pick_inbox_id",
        "fk_smt_inbound_handoff_source_items_source_pick_inbox_i_cf89",
        "fk_smt_inbound_handoff_source_items_source_pick_inbox_runtime",
        """
        UPDATE wes_biz.smt_inbound_handoff_source_items AS dependent
        SET source_pick_inbox_id = runtime.id
        FROM wes_runtime.runtime_inbox AS runtime
        WHERE dependent.source_pick_inbox_id IS NOT NULL
          AND runtime.provider_code = :provider_code
          AND runtime.event_type = :event_type
          AND runtime.source_event_id = 'legacy-workline-inbox:' || dependent.source_pick_inbox_id::text
        """,
    ),
)


def upgrade() -> None:
    """迁移所有引用并删除旧 Inbox 表。"""
    op.add_column(
        "runtime_inbox",
        sa.Column("workline_session_id", sa.BigInteger(), nullable=True),
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

    # 先解除旧 FK，再把每个被引用的 legacy 行映射为不可执行的 audit-only RuntimeInbox 证据。
    for table_name, _column_name, old_constraint, _new_constraint, _update_statement in _DEPENDENT_FOREIGN_KEYS:
        op.drop_constraint(old_constraint, table_name, schema=BIZ_SCHEMA, type_="foreignkey")

    op.execute(
        sa.text(
            """
            WITH referenced_legacy_ids AS (
                SELECT inbox_id AS legacy_id FROM wes_biz.workline_diagnostics WHERE inbox_id IS NOT NULL
                UNION
                SELECT source_inbox_id FROM wes_biz.runtime_holds WHERE source_inbox_id IS NOT NULL
                UNION
                SELECT source_pick_inbox_id
                FROM wes_biz.smt_inbound_handoff_source_items
                WHERE source_pick_inbox_id IS NOT NULL
            )
            INSERT INTO wes_runtime.runtime_inbox (
                workline_session_id, provider_code, event_type, source_event_id,
                status, attempt_count, max_retries, last_error_code,
                last_error_message, received_at, failed_at
            )
            SELECT legacy.session_id,
                   :provider_code,
                   :event_type,
                   'legacy-workline-inbox:' || legacy.id::text,
                   'DEAD_LETTER',
                   legacy.attempt_count,
                   greatest(legacy.max_attempts, 1),
                   :audit_code,
                   'Legacy WorklineInbox ' || legacy.id::text || ' retained as audit-only reference evidence',
                   floor(extract(epoch FROM legacy.received_at) * 1000)::bigint,
                   floor(extract(epoch FROM legacy.received_at) * 1000)::bigint
            FROM wes_biz.workline_inbox AS legacy
            JOIN referenced_legacy_ids AS referenced ON referenced.legacy_id = legacy.id
            """
        ).bindparams(
            provider_code=LEGACY_PROVIDER_CODE,
            event_type=LEGACY_EVENT_TYPE,
            audit_code=PRE_CUTOVER_AUDIT_ONLY,
        )
    )

    for table_name, column_name, _old_constraint, new_constraint, update_statement in _DEPENDENT_FOREIGN_KEYS:
        op.execute(
            sa.text(update_statement).bindparams(
                provider_code=LEGACY_PROVIDER_CODE,
                event_type=LEGACY_EVENT_TYPE,
            )
        )
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
    """仅在无 RuntimeInbox 引用数据时恢复旧空表；任何有损降级都 fail-closed。"""
    bind = op.get_bind()
    referenced_count = bind.execute(
        sa.text(
            """
            SELECT
                (SELECT count(*) FROM wes_biz.workline_diagnostics WHERE inbox_id IS NOT NULL)
              + (SELECT count(*) FROM wes_biz.runtime_holds WHERE source_inbox_id IS NOT NULL)
              + (SELECT count(*) FROM wes_biz.smt_inbound_handoff_source_items WHERE source_pick_inbox_id IS NOT NULL)
              + (SELECT count(*) FROM wes_runtime.runtime_inbox WHERE workline_session_id IS NOT NULL)
            """
        )
    ).scalar_one()
    if referenced_count:
        raise RuntimeError(
            f"Revision B downgrade refused: {referenced_count} RuntimeInbox reference(s) would lose identity"
        )

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

    for table_name, column_name, old_constraint, new_constraint, _update_statement in _DEPENDENT_FOREIGN_KEYS:
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
    op.drop_column("runtime_inbox", "workline_session_id", schema=RUNTIME_SCHEMA)
