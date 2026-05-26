"""workline record diagnostics

Revision ID: a8c9d0e1f2a3
Revises: f7a8b9c0d1e2
Create Date: 2026-04-27 12:00:00.000000+08:00

"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a8c9d0e1f2a3"
down_revision: Union[str, Sequence[str], None] = "f7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    op.execute("ALTER TABLE wes_biz.callback_logs ADD COLUMN IF NOT EXISTS trace_id VARCHAR(100)")
    op.execute("ALTER TABLE wes_biz.callback_logs ADD COLUMN IF NOT EXISTS event_id VARCHAR(200)")
    op.execute("ALTER TABLE wes_biz.callback_logs ADD COLUMN IF NOT EXISTS causation_id VARCHAR(200)")

    op.execute("ALTER TABLE wes_biz.workline_inbox ADD COLUMN IF NOT EXISTS trace_id VARCHAR(100)")
    op.execute("ALTER TABLE wes_biz.workline_inbox ADD COLUMN IF NOT EXISTS event_id VARCHAR(200)")
    op.execute("ALTER TABLE wes_biz.workline_inbox ADD COLUMN IF NOT EXISTS causation_id VARCHAR(200)")

    op.execute("ALTER TABLE wes_biz.workline_sessions ADD COLUMN IF NOT EXISTS trace_id VARCHAR(100)")

    op.execute("ALTER TABLE wes_biz.device_commands ADD COLUMN IF NOT EXISTS trace_id VARCHAR(100)")
    op.execute("ALTER TABLE wes_biz.device_commands ADD COLUMN IF NOT EXISTS event_id VARCHAR(200)")
    op.execute("ALTER TABLE wes_biz.device_commands ADD COLUMN IF NOT EXISTS causation_id VARCHAR(200)")
    op.execute("ALTER TABLE wes_biz.device_commands ADD COLUMN IF NOT EXISTS session_id_int BIGINT")
    op.execute("""
        UPDATE wes_biz.device_commands
        SET session_id_int = session_id::BIGINT
        WHERE session_id_int IS NULL
          AND session_id ~ '^[0-9]+$'
    """)
    op.execute("ALTER TABLE wes_biz.workline_timelines ADD COLUMN IF NOT EXISTS trace_id VARCHAR(100)")

    for table in (
        "callback_logs",
        "workline_inbox",
        "workline_sessions",
        "device_commands",
        "workline_timelines",
    ):
        op.execute(f"DROP INDEX IF EXISTS wes_biz.ix_wes_biz_{table}_correlation_id")
        op.execute(f"ALTER TABLE wes_biz.{table} DROP COLUMN IF EXISTS correlation_id")

    op.execute("""
        CREATE TABLE IF NOT EXISTS wes_biz.workline_diagnostics (
            id BIGSERIAL PRIMARY KEY,
            diagnostic_key VARCHAR(300) NOT NULL UNIQUE,
            trace_id VARCHAR(100),
            request_id VARCHAR(200),
            event_id VARCHAR(200),
            causation_id VARCHAR(200),
            session_id BIGINT REFERENCES wes_biz.workline_sessions(id),
            inbox_id BIGINT REFERENCES wes_biz.workline_inbox(id),
            outbox_id BIGINT,
            command_code VARCHAR(200),
            device_code VARCHAR(100),
            workline_id BIGINT REFERENCES wes_biz.work_lines(id),
            plugin_key VARCHAR(100),
            diagnostic_code VARCHAR(100) NOT NULL,
            error_domain VARCHAR(100) NOT NULL,
            severity VARCHAR(50) NOT NULL,
            recoverability VARCHAR(100) NOT NULL,
            problem_class VARCHAR(50) NOT NULL,
            owner VARCHAR(100) NOT NULL,
            status VARCHAR(50) NOT NULL DEFAULT 'ACTIVE',
            message TEXT NOT NULL,
            operator_action TEXT,
            technical_summary TEXT,
            docs_anchor VARCHAR(200),
            next_steps_json JSONB NOT NULL DEFAULT '[]'::JSONB,
            evidence_json JSONB NOT NULL DEFAULT '{}'::JSONB,
            card_json JSONB NOT NULL DEFAULT '{}'::JSONB,
            resolved_at TIMESTAMP WITHOUT TIME ZONE,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
            updated_at TIMESTAMP WITHOUT TIME ZONE
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS wes_biz.workline_dispatch_attempts (
            id BIGSERIAL PRIMARY KEY,
            outbox_id BIGINT NOT NULL,
            dispatch_key VARCHAR(200) NOT NULL,
            attempt_no INTEGER NOT NULL,
            lease_token VARCHAR(240) NOT NULL UNIQUE,
            status VARCHAR(50) NOT NULL DEFAULT 'DISPATCHING',
            target_type VARCHAR(100),
            target_code VARCHAR(200),
            started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            finalized_at TIMESTAMP WITHOUT TIME ZONE,
            error_message TEXT,
            response_json JSONB NOT NULL DEFAULT '{}'::JSONB,
            trace_json JSONB NOT NULL DEFAULT '{}'::JSONB,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
            updated_at TIMESTAMP WITHOUT TIME ZONE,
            CONSTRAINT uq_workline_dispatch_attempts_outbox_attempt UNIQUE (outbox_id, attempt_no)
        )
    """)

    for table, column in (
        ("callback_logs", "trace_id"),
        ("callback_logs", "event_id"),
        ("workline_inbox", "trace_id"),
        ("workline_inbox", "event_id"),
        ("workline_sessions", "trace_id"),
        ("device_commands", "trace_id"),
        ("device_commands", "event_id"),
        ("device_commands", "session_id_int"),
        ("workline_timelines", "trace_id"),
        ("workline_diagnostics", "trace_id"),
        ("workline_diagnostics", "diagnostic_code"),
        ("workline_diagnostics", "status"),
        ("workline_dispatch_attempts", "outbox_id"),
        ("workline_dispatch_attempts", "dispatch_key"),
    ):
        op.execute(f"CREATE INDEX IF NOT EXISTS ix_wes_biz_{table}_{column} ON wes_biz.{table} ({column})")

    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_workline_inbox_idempotency_key
        ON wes_biz.workline_inbox (idempotency_key)
        WHERE idempotency_key IS NOT NULL
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_workline_timelines_session_seq_no
        ON wes_biz.workline_timelines (session_id, seq_no)
    """)


def downgrade() -> None:
    """Downgrade schema."""

    op.execute("DROP INDEX IF EXISTS wes_biz.uq_workline_timelines_session_seq_no")
    op.execute("DROP INDEX IF EXISTS wes_biz.uq_workline_inbox_idempotency_key")
    op.execute("DROP TABLE IF EXISTS wes_biz.workline_dispatch_attempts")
    op.execute("DROP TABLE IF EXISTS wes_biz.workline_diagnostics")
    op.execute("ALTER TABLE wes_biz.workline_timelines DROP COLUMN IF EXISTS trace_id")
    op.execute("ALTER TABLE wes_biz.device_commands DROP COLUMN IF EXISTS session_id_int")
    op.execute("ALTER TABLE wes_biz.device_commands DROP COLUMN IF EXISTS causation_id")
    op.execute("ALTER TABLE wes_biz.device_commands DROP COLUMN IF EXISTS event_id")
    op.execute("ALTER TABLE wes_biz.device_commands DROP COLUMN IF EXISTS trace_id")
    op.execute("ALTER TABLE wes_biz.workline_sessions DROP COLUMN IF EXISTS trace_id")
    op.execute("ALTER TABLE wes_biz.workline_inbox DROP COLUMN IF EXISTS causation_id")
    op.execute("ALTER TABLE wes_biz.workline_inbox DROP COLUMN IF EXISTS event_id")
    op.execute("ALTER TABLE wes_biz.workline_inbox DROP COLUMN IF EXISTS trace_id")
    op.execute("ALTER TABLE wes_biz.callback_logs DROP COLUMN IF EXISTS causation_id")
    op.execute("ALTER TABLE wes_biz.callback_logs DROP COLUMN IF EXISTS event_id")
    op.execute("ALTER TABLE wes_biz.callback_logs DROP COLUMN IF EXISTS trace_id")
